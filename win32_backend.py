"""Windows input/clipboard boundary. Importing this module never sends input."""
from __future__ import annotations

import ctypes as C
import os
import time

from core import InputFailure, Target

DWORD = C.c_uint32
LONG = C.c_int32
WORD = C.c_uint16
UINT = C.c_uint32
ULONG_PTR = C.c_size_t
HWND = C.c_void_p
HANDLE = C.c_void_p
BOOL = C.c_int
LPARAM = C.c_ssize_t


class POINT(C.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(C.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class GUITHREADINFO(C.Structure):
    _fields_ = [("cbSize", DWORD), ("flags", DWORD), ("hwndActive", HWND),
                ("hwndFocus", HWND), ("hwndCapture", HWND), ("hwndMenuOwner", HWND),
                ("hwndMoveSize", HWND), ("hwndCaret", HWND), ("rcCaret", RECT)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", DWORD), ("data", INPUTUNION)]


def key_input(vk: int = 0, *, scan: int = 0, flags: int = 0) -> INPUT:
    result = INPUT()
    result.type = 1  # INPUT_KEYBOARD
    result.ki = KEYBDINPUT(vk, scan, flags, 0, 0x48545950)
    return result


def unicode_inputs(text: str) -> list[INPUT]:
    encoded = text.encode("utf-16-le")
    result = []
    for offset in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[offset:offset + 2], "little")
        result.extend((key_input(scan=unit, flags=4), key_input(scan=unit, flags=6)))
    return result


class WinAPI:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("HumanType runs on Windows 10 or 11.")
        self.user32 = C.WinDLL("user32", use_last_error=True)
        self.kernel32 = C.WinDLL("kernel32", use_last_error=True)
        definitions = {
            "GetForegroundWindow": (HWND, []),
            "GetWindowThreadProcessId": (DWORD, [HWND, C.POINTER(DWORD)]),
            "GetGUIThreadInfo": (BOOL, [DWORD, C.POINTER(GUITHREADINFO)]),
            "GetWindowTextW": (C.c_int, [HWND, C.c_wchar_p, C.c_int]),
            "GetClassNameW": (C.c_int, [HWND, C.c_wchar_p, C.c_int]),
            "IsWindow": (BOOL, [HWND]),
            "IsWindowVisible": (BOOL, [HWND]),
            "IsIconic": (BOOL, [HWND]),
            "ShowWindowAsync": (BOOL, [HWND, C.c_int]),
            "SetForegroundWindow": (BOOL, [HWND]),
            "GetAsyncKeyState": (C.c_short, [C.c_int]),
            "GetKeyState": (C.c_short, [C.c_int]),
            "GetKeyboardLayout": (HANDLE, [DWORD]),
            "VkKeyScanExW": (C.c_short, [C.c_wchar, HANDLE]),
            "MapVirtualKeyExW": (UINT, [UINT, UINT, HANDLE]),
            "ToUnicodeEx": (C.c_int, [UINT, UINT, C.POINTER(C.c_ubyte), C.c_wchar_p, C.c_int, UINT, HANDLE]),
            "SendInput": (UINT, [UINT, C.POINTER(INPUT), C.c_int]),
            "OpenClipboard": (BOOL, [HWND]),
            "CloseClipboard": (BOOL, []),
            "IsClipboardFormatAvailable": (BOOL, [UINT]),
            "GetClipboardData": (HANDLE, [UINT]),
        }
        for name, (result, args) in definitions.items():
            function = getattr(self.user32, name)
            function.restype = result
            function.argtypes = args
            setattr(self, name, function)
        for name, result, args in (
            ("GlobalLock", C.c_void_p, [HANDLE]),
            ("GlobalUnlock", BOOL, [HANDLE]),
            ("GlobalSize", C.c_size_t, [HANDLE]),
        ):
            function = getattr(self.kernel32, name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)


class WindowsDesktop:
    def __init__(self, api=None):
        self.api = api if api is not None else WinAPI()
        self.pid = os.getpid()

    def capture_foreground(self) -> Target | None:
        window = self.api.GetForegroundWindow()
        if not window or not self.api.IsWindowVisible(window):
            return None
        pid = DWORD()
        thread = self.api.GetWindowThreadProcessId(window, C.byref(pid))
        if not thread or pid.value == self.pid:
            return None
        name = C.create_unicode_buffer(256)
        self.api.GetClassNameW(window, name, len(name))
        if name.value in {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}:
            return None
        info = GUITHREADINFO()
        info.cbSize = C.sizeof(info)
        if not self.api.GetGUIThreadInfo(0, C.byref(info)) or not info.hwndFocus:
            return None
        if self.api.GetForegroundWindow() != window:
            return None
        title = C.create_unicode_buffer(512)
        self.api.GetWindowTextW(window, title, len(title))
        return Target(int(window), int(info.hwndFocus), pid.value, int(thread), title.value or "Untitled window")

    def is_valid(self, target: Target | None) -> bool:
        if target is None or not self.api.IsWindow(target.window):
            return False
        pid = DWORD()
        thread = self.api.GetWindowThreadProcessId(target.window, C.byref(pid))
        return (pid.value == target.process_id and pid.value != self.pid
                and thread == target.thread_id and bool(self.api.IsWindow(target.focus)))

    def has_focus(self, target: Target) -> bool:
        if not self.is_valid(target) or self.api.GetForegroundWindow() != target.window:
            return False
        info = GUITHREADINFO()
        info.cbSize = C.sizeof(info)
        return bool(self.api.GetGUIThreadInfo(0, C.byref(info)) and info.hwndFocus == target.focus)

    def activate(self, target: Target) -> bool:
        if not self.is_valid(target):
            return False
        if self.api.IsIconic(target.window):
            self.api.ShowWindowAsync(target.window, 9)
        # A request, not proof of success: the controller waits and checks focus later.
        return bool(self.api.SetForegroundWindow(target.window))

    def modifiers_or_mouse_down(self) -> bool:
        return any(self.api.GetAsyncKeyState(key) & 0x8000
                   for key in (1, 2, 4, 16, 17, 18, 91, 92))

    def _physical_ascii(self, char: str, target: Target) -> list[INPUT] | None:
        if len(char) != 1 or not 32 <= ord(char) <= 126:
            return None
        layout = self.api.GetKeyboardLayout(target.thread_id)
        mapped = self.api.VkKeyScanExW(char, layout)
        if mapped == -1:
            return None
        vk, shift_state = mapped & 255, (mapped >> 8) & 255
        # Never turn ordinary text into Ctrl/Alt shortcuts or AltGr chords.
        if shift_state & ~1:
            return None
        shift = bool(shift_state & 1)
        caps = bool(self.api.GetKeyState(0x14) & 1)
        if char.isalpha() and caps:
            shift = not shift
        state = (C.c_ubyte * 256)()
        state[0x10] = 0x80 if shift else 0
        state[0x14] = 1 if caps else 0
        translated = C.create_unicode_buffer(8)
        scan = self.api.MapVirtualKeyExW(vk, 0, layout)
        # Flag 4 prevents ToUnicodeEx from changing the keyboard's dead-key state.
        count = self.api.ToUnicodeEx(vk, scan, state, translated, len(translated), 4, layout)
        if count != 1 or translated.value != char:
            return None
        result = [key_input(0x10)] if shift else []
        result.extend((key_input(vk, scan=scan), key_input(vk, scan=scan, flags=2)))
        if shift:
            result.append(key_input(0x10, flags=2))
        return result

    def build_inputs(self, char: str, target: Target, newline_mode: str) -> list[INPUT]:
        if char == "\n":
            if newline_mode == "unicode":
                return unicode_inputs("\r")
            result = [key_input(0x10)] if newline_mode == "shift_enter" else []
            result.extend((key_input(0x0D), key_input(0x0D, flags=2)))
            if newline_mode == "shift_enter":
                result.append(key_input(0x10, flags=2))
            return result
        # A literal tab packet avoids issuing a Tab navigation shortcut.
        return self._physical_ascii(char, target) or unicode_inputs(char)

    def send_character(self, char: str, target: Target, newline_mode: str) -> None:
        inputs = self.build_inputs(char, target, newline_mode)
        if not inputs:
            return
        if not self.has_focus(target):
            raise InputFailure("Focus changed before typing. Use Continue to return to the saved field.")
        array = (INPUT * len(inputs))(*inputs)
        C.set_last_error(0)
        sent = int(self.api.SendInput(len(inputs), array, C.sizeof(INPUT)))
        if sent != len(inputs):
            error = C.get_last_error()
            if sent:
                # Release only modifiers this app introduced, preventing a stuck Shift key.
                releases = [key_input(0x10, flags=2)] if any(i.ki.wVk == 0x10 for i in inputs[:sent]) else []
                if releases:
                    cleanup = (INPUT * len(releases))(*releases)
                    self.api.SendInput(len(releases), cleanup, C.sizeof(INPUT))
                raise InputFailure("Windows accepted only part of a keystroke. Check the field and Paste the remaining text before restarting.", uncertain=True)
            suffix = f" (Windows error {error})" if error else ""
            raise InputFailure("Windows blocked typing. Run both apps normally and click an editable field during the Continue countdown." + suffix)

    def read_clipboard_text(self) -> str:
        for attempt in range(6):
            if self.api.OpenClipboard(None):
                break
            if attempt == 5:
                raise RuntimeError("The clipboard is busy. Wait a moment and press Paste again.")
            time.sleep(0.015)
        try:
            if not self.api.IsClipboardFormatAvailable(13):
                raise RuntimeError("The clipboard has no text. Copy text first, then press Paste.")
            handle = self.api.GetClipboardData(13)
            if not handle:
                raise RuntimeError("Windows could not read the clipboard. Copy the text again.")
            size = self.api.GlobalSize(handle)
            if size > 2_000_004:
                raise RuntimeError("Copy less than 1 million characters at a time.")
            pointer = self.api.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("The clipboard could not be opened. Press Paste again.")
            try:
                return C.wstring_at(pointer, size // 2).split("\0", 1)[0] if size else ""
            finally:
                self.api.GlobalUnlock(handle)
        finally:
            self.api.CloseClipboard()

