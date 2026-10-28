"""Temporary physical-key filtering for the active typing field.

No hook is installed at import or construction time. Injected keyboard events
always pass through, and a short heartbeat lease makes failures release input.
"""
from __future__ import annotations

import ctypes as C
import os
import threading
import time

from core import Target
from win32_backend import BOOL, DWORD, GUITHREADINFO, HWND, LPARAM, POINT, UINT, ULONG_PTR

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_UP = 0x80

BLOCKED_KEYS = frozenset(
    (*range(0x41, 0x5B), *range(0x30, 0x3A), *range(0x60, 0x70),
     *range(0xBA, 0xC1), *range(0xDB, 0xE3), *range(33, 41),
     0x20, 8, 9, 13, 20, 45, 46)
)
TRIGGER_KEYS = frozenset((*range(0x41, 0x5B), 0x20))
SYSTEM_KEYS = frozenset((17, 18, 91, 92, 0xA2, 0xA3, 0xA4, 0xA5, *range(0x70, 0x88)))


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", DWORD), ("scanCode", DWORD), ("flags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSG(C.Structure):
    _fields_ = [("hwnd", HWND), ("message", UINT), ("wParam", ULONG_PTR),
                ("lParam", LPARAM), ("time", DWORD), ("pt", POINT), ("lPrivate", DWORD)]


HOOKPROC = getattr(C, "WINFUNCTYPE", C.CFUNCTYPE)(C.c_ssize_t, C.c_int, ULONG_PTR, LPARAM)


class KeyboardPolicy:
    """Pure event policy; it never calls Windows or records typed text."""

    def __init__(self, *, clock=time.monotonic, lease_seconds=0.5):
        self.clock = clock
        self.lease_seconds = min(0.5, max(0.01, float(lease_seconds)))
        self._lease: tuple[int, int, float] | None = None
        self._blocked_down: set[int] = set()
        self._pause_requested = False
        self._keypresses = 0
        self._lock = threading.RLock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._lease is not None and self.clock() <= self._lease[2]

    def activate(self, target: Target) -> None:
        with self._lock:
            now = self.clock()
            if self._lease is not None and now > self._lease[2]:
                self._lease = None
                self._pause_requested = True
                self._keypresses = 0
            if self._pause_requested:
                raise RuntimeError("Keyboard input was released. Pause and resume the typing job to continue.")
            self._lease = (target.window, target.focus, now + self.lease_seconds)

    def deactivate(self) -> None:
        with self._lock:
            self._lease = None
            self._keypresses = 0

    def request_pause(self) -> None:
        with self._lock:
            self._lease = None
            self._pause_requested = True
            self._keypresses = 0

    def consume_pause_request(self) -> bool:
        with self._lock:
            requested = self._pause_requested
            self._pause_requested = False
            return requested

    def consume_keypresses(self) -> int:
        with self._lock:
            if self._lease is not None and self.clock() > self._lease[2]:
                self._lease = None
                self._pause_requested = True
            if self._lease is None:
                self._keypresses = 0
                return 0
            count = self._keypresses
            self._keypresses = 0
            return count

    def handle(self, vk: int, flags: int, message: int, foreground: int, focus: int) -> bool:
        # The app's own SendInput packets must never be suppressed or mutate the
        # physical-key pairing state (including synthetic Shift up events).
        if flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
            return False
        with self._lock:
            if self._lease is not None:
                window, control, expires = self._lease
                if self.clock() > expires or foreground != window or focus != control:
                    self._lease = None
                    self._pause_requested = True
                    self._keypresses = 0
            key_up = message in (WM_KEYUP, WM_SYSKEYUP) or bool(flags & LLKHF_UP)
            if key_up:
                if vk in self._blocked_down:
                    self._blocked_down.discard(vk)
                    return True
                return False
            if message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
                return False
            # Keep the whole physical press paired. If its original down was
            # swallowed before Pause, do not pass an autorepeat down and then
            # swallow its eventual up, which could leave a logical key stuck.
            if vk in self._blocked_down:
                return True
            if self._lease is None:
                return False
            if vk == 27:  # Escape pauses without reaching the destination field.
                self._lease = None
                self._pause_requested = True
                self._keypresses = 0
                self._blocked_down.add(vk)
                return True
            if vk in SYSTEM_KEYS:
                self._lease = None
                self._pause_requested = True
                self._keypresses = 0
                return False
            if vk in BLOCKED_KEYS:
                self._blocked_down.add(vk)
                if vk in TRIGGER_KEYS:
                    self._keypresses += 1
                return True
            return False


class NativeAPI:
    """The small native boundary is injectable so tests never install a hook."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Keyboard filtering requires Windows 10 or 11.")
        self.user32 = C.WinDLL("user32", use_last_error=True)
        self.kernel32 = C.WinDLL("kernel32", use_last_error=True)
        definitions = {
            "SetWindowsHookExW": (C.c_void_p, [C.c_int, HOOKPROC, C.c_void_p, DWORD]),
            "UnhookWindowsHookEx": (BOOL, [C.c_void_p]),
            "CallNextHookEx": (C.c_ssize_t, [C.c_void_p, C.c_int, ULONG_PTR, LPARAM]),
            "GetMessageW": (BOOL, [C.POINTER(MSG), HWND, UINT, UINT]),
            "PeekMessageW": (BOOL, [C.POINTER(MSG), HWND, UINT, UINT, UINT]),
            "PostThreadMessageW": (BOOL, [DWORD, UINT, ULONG_PTR, LPARAM]),
            "GetForegroundWindow": (HWND, []),
            "GetGUIThreadInfo": (BOOL, [DWORD, C.POINTER(GUITHREADINFO)]),
        }
        for name, (result, args) in definitions.items():
            function = getattr(self.user32, name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)
        for name, result, args in (
            ("GetModuleHandleW", C.c_void_p, [C.c_wchar_p]),
            ("GetCurrentThreadId", DWORD, []),
        ):
            function = getattr(self.kernel32, name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)


class KeyboardGuard:
    def __init__(self, api=None, *, clock=time.monotonic):
        self.api = api
        self.policy = KeyboardPolicy(clock=clock)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._callback = None
        self._ready = threading.Event()
        self._startup_error: str | None = None
        self._closed = False
        self._lifecycle_lock = threading.Lock()

    @property
    def active(self) -> bool:
        return not self._closed and self.policy.active

    def activate(self, target: Target) -> None:
        if self._closed:
            raise RuntimeError("Keyboard filtering is closed.")
        self._ensure_started()
        self.policy.activate(target)

    def deactivate(self) -> None:
        self.policy.deactivate()

    def consume_pause_request(self) -> bool:
        return self.policy.consume_pause_request()

    def consume_keypresses(self) -> int:
        return self.policy.consume_keypresses()

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Keyboard filtering is closed.")
            if self._thread is None:
                if self.api is None:
                    self.api = NativeAPI()
                self._ready.clear()
                self._startup_error = None
                self._thread = threading.Thread(target=self._run, name="HumanTypeKeyboardGuard", daemon=True)
                self._thread.start()
            thread = self._thread
        if not self._ready.wait(timeout=0.8):
            self.policy.request_pause()
            raise RuntimeError("Windows did not enable keyboard filtering. Typing is paused.")
        if self._startup_error or not thread.is_alive() or not self._hook:
            self.policy.deactivate()
            raise RuntimeError(self._startup_error or "Windows keyboard filtering stopped. Typing is paused.")

    def _hook_callback(self, code: int, message: int, event_pointer: int) -> int:
        if code < 0 or self._closed:
            return self.api.CallNextHookEx(self._hook, code, message, event_pointer)
        try:
            event = C.cast(event_pointer, C.POINTER(KBDLLHOOKSTRUCT)).contents
            if event.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                return self.api.CallNextHookEx(self._hook, code, message, event_pointer)
            foreground = self.api.GetForegroundWindow() or 0
            info = GUITHREADINFO()
            info.cbSize = C.sizeof(info)
            focus = info.hwndFocus if self.api.GetGUIThreadInfo(0, C.byref(info)) else 0
            # Confirm the foreground did not change while taking the snapshot.
            if self.api.GetForegroundWindow() != foreground:
                foreground = 0
            if self.policy.handle(event.vkCode, event.flags, message, int(foreground), int(focus or 0)):
                return 1
        except Exception:
            # ctypes callback exceptions must not leave the keyboard suppressed.
            self.policy.request_pause()
        return self.api.CallNextHookEx(self._hook, code, message, event_pointer)

    def _run(self) -> None:
        message = MSG()
        try:
            self._thread_id = int(self.api.GetCurrentThreadId())
            self.api.PeekMessageW(C.byref(message), None, 0, 0, 0)
            if self._closed:
                return
            self._callback = HOOKPROC(self._hook_callback)
            self._hook = self.api.SetWindowsHookExW(13, self._callback, self.api.GetModuleHandleW(None), 0)
            if not self._hook:
                self._startup_error = "Windows could not enable keyboard filtering. Typing is paused."
                return
            self._ready.set()
            while not self._closed:
                received = self.api.GetMessageW(C.byref(message), None, 0, 0)
                if received <= 0:
                    if received < 0:
                        self._startup_error = "Windows keyboard filtering stopped. Typing is paused."
                    break
        except Exception as error:
            self._startup_error = f"Keyboard filtering could not start: {error}"
        finally:
            self.policy.deactivate()
            if not self._closed:
                self.policy.request_pause()
            if self._hook:
                try:
                    self.api.UnhookWindowsHookEx(self._hook)
                except Exception:
                    # The policy is already inactive; process exit also removes
                    # the native hook even if Windows refuses explicit cleanup.
                    pass
                finally:
                    self._hook = None
            self._ready.set()

    def close(self) -> None:
        self.policy.deactivate()
        self._closed = True
        thread = self._thread
        if thread and thread.is_alive():
            if self._thread_id:
                self.api.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if thread is not threading.current_thread():
                thread.join(timeout=0.8)
