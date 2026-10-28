"""Native-boundary tests using an in-memory fake API; no Windows input is sent."""
from __future__ import annotations

from collections import deque
import ctypes as C
import unittest
from unittest.mock import patch

from core import InputFailure, Target
from win32_backend import (
    DWORD, GUITHREADINFO, INPUT, KEYBDINPUT, MOUSEINPUT,
    WindowsDesktop, key_input, unicode_inputs,
)


TARGET = Target(101, 102, 11, 12, "Editor A")


def packets(events):
    return [(event.type, event.ki.wVk, event.ki.wScan, event.ki.dwFlags) for event in events]


class FakeAPI:
    """Enough Win32 behavior to exercise packet construction and cleanup paths."""

    def __init__(self):
        self.foreground = TARGET.window
        self.foreground_answers = deque()
        self.focus = TARGET.focus
        self.pid = TARGET.process_id
        self.thread = TARGET.thread_id
        self.valid_windows = {TARGET.window, TARGET.focus}
        self.visible = True
        self.gui_ok = True
        self.window_class = "Edit"
        self.title = TARGET.title
        self.iconic = False
        self.activation_requests = []
        self.show_requests = []
        self.keys_down = set()
        self.caps = False
        self.mapping = {"a": 0x0041, "A": 0x0141, " ": 0x0020, "!": 0x0131, "^": 0x0136}
        self.last_character = ""
        self.mapping_calls = []
        self.translation_count = 1
        self.translation_override = None
        self.translation_calls = []
        self.send_results = deque()
        self.send_calls = []
        self.send_error = 0
        self.clipboard_open_answers = deque()
        self.clipboard_opens = 0
        self.clipboard_closes = 0
        self.clipboard_has_text = True
        self.clipboard_handle = 77
        self.clipboard_buffer = C.create_unicode_buffer("Clipboard café 🙂")
        self.clipboard_size = C.sizeof(self.clipboard_buffer)
        self.clipboard_lock_ok = True
        self.clipboard_unlocks = []

    def GetForegroundWindow(self):
        return self.foreground_answers.popleft() if self.foreground_answers else self.foreground

    def GetWindowThreadProcessId(self, window, pid_pointer):
        C.cast(pid_pointer, C.POINTER(DWORD)).contents.value = self.pid
        return self.thread

    def GetGUIThreadInfo(self, thread, info_pointer):
        info = C.cast(info_pointer, C.POINTER(GUITHREADINFO)).contents
        if info.cbSize != C.sizeof(GUITHREADINFO):
            raise AssertionError("GUITHREADINFO.cbSize not initialized")
        info.hwndFocus = self.focus
        return self.gui_ok

    def GetClassNameW(self, window, buffer, size):
        buffer.value = self.window_class
        return len(self.window_class)

    def GetWindowTextW(self, window, buffer, size):
        buffer.value = self.title
        return len(self.title)

    def IsWindow(self, window):
        return window in self.valid_windows

    def IsWindowVisible(self, window):
        return self.visible

    def IsIconic(self, window):
        return self.iconic

    def ShowWindowAsync(self, window, command):
        self.show_requests.append((window, command))
        return True

    def SetForegroundWindow(self, window):
        self.activation_requests.append(window)
        return False  # A request must not be mistaken for synchronous focus.

    def GetAsyncKeyState(self, key):
        return 0x8000 if key in self.keys_down else 0

    def GetKeyState(self, key):
        return 1 if key == 0x14 and self.caps else 0

    def GetKeyboardLayout(self, thread):
        return 0x0409

    def VkKeyScanExW(self, character, layout):
        self.mapping_calls.append((character, layout))
        self.last_character = character
        return self.mapping.get(character, -1)

    def MapVirtualKeyExW(self, key, mode, layout):
        return 0x1E

    def ToUnicodeEx(self, key, scan, state, translated, size, flags, layout):
        self.translation_calls.append((key, scan, state[0x10], state[0x14], flags, layout))
        translated.value = self.last_character if self.translation_override is None else self.translation_override
        return self.translation_count

    def SendInput(self, count, events, size):
        self.send_calls.append((count, packets(events[:count]), size))
        C.set_last_error(self.send_error)
        return self.send_results.popleft() if self.send_results else count

    def OpenClipboard(self, owner):
        self.clipboard_opens += 1
        return self.clipboard_open_answers.popleft() if self.clipboard_open_answers else True

    def CloseClipboard(self):
        self.clipboard_closes += 1
        return True

    def IsClipboardFormatAvailable(self, format_id):
        if format_id != 13:
            raise AssertionError("Expected CF_UNICODETEXT")
        return self.clipboard_has_text

    def GetClipboardData(self, format_id):
        return self.clipboard_handle

    def GlobalSize(self, handle):
        return self.clipboard_size

    def GlobalLock(self, handle):
        return C.addressof(self.clipboard_buffer) if self.clipboard_lock_ok else 0

    def GlobalUnlock(self, handle):
        self.clipboard_unlocks.append(handle)
        return True

class NativeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.desktop = WindowsDesktop(api=self.api)

    def test_input_structures_match_windows_native_abi(self):
        if C.sizeof(C.c_void_p) == 8:
            expected = {INPUT: 40, KEYBDINPUT: 24, MOUSEINPUT: 32, GUITHREADINFO: 72}
            self.assertEqual(8, INPUT.data.offset)
        else:
            expected = {INPUT: 28, KEYBDINPUT: 16, MOUSEINPUT: 24, GUITHREADINFO: 48}
            self.assertEqual(4, INPUT.data.offset)
        for structure, size in expected.items():
            with self.subTest(structure=structure.__name__):
                self.assertEqual(size, C.sizeof(structure))

    def test_unicode_emoji_is_encoded_as_utf16_surrogate_packets(self):
        self.assertEqual(
            [(1, 0, 0xD83D, 4), (1, 0, 0xD83D, 6), (1, 0, 0xDE42, 4), (1, 0, 0xDE42, 6)],
            packets(unicode_inputs("🙂")),
        )

    def test_unicode_tab_is_a_literal_packet_not_a_navigation_key(self):
        events = self.desktop.build_inputs("\t", TARGET, "shift_enter")
        self.assertEqual([(1, 0, 9, 4), (1, 0, 9, 6)], packets(events))
        self.assertEqual([], self.api.mapping_calls)

    def test_shift_enter_uses_balanced_modifier_and_return_events(self):
        events = self.desktop.build_inputs("\n", TARGET, "shift_enter")
        self.assertEqual([(1, 16, 0, 0), (1, 13, 0, 0), (1, 13, 0, 2), (1, 16, 0, 2)], packets(events))

    def test_enter_mode_sends_return_without_shift(self):
        self.assertEqual([(1, 13, 0, 0), (1, 13, 0, 2)], packets(self.desktop.build_inputs("\n", TARGET, "enter")))

    def test_text_only_newline_is_a_carriage_return_packet(self):
        self.assertEqual([(1, 0, 13, 4), (1, 0, 13, 6)], packets(self.desktop.build_inputs("\n", TARGET, "unicode")))

    def test_plain_ascii_uses_verified_physical_key_events(self):
        events = self.desktop.build_inputs("a", TARGET, "shift_enter")
        self.assertEqual([(1, 65, 0x1E, 0), (1, 65, 0x1E, 2)], packets(events))
        self.assertEqual([("a", 0x0409)], self.api.mapping_calls)
        self.assertEqual(4, self.api.translation_calls[0][4], "ToUnicodeEx must not mutate dead-key state")

    def test_uppercase_ascii_balances_shift(self):
        events = self.desktop.build_inputs("A", TARGET, "shift_enter")
        self.assertEqual([(1, 16, 0, 0), (1, 65, 0x1E, 0), (1, 65, 0x1E, 2), (1, 16, 0, 2)], packets(events))
        self.assertEqual(0x80, self.api.translation_calls[0][2])

    def test_caps_lock_inverts_shift_for_ascii_letters(self):
        self.api.caps = True
        upper = self.desktop.build_inputs("A", TARGET, "shift_enter")
        self.assertEqual([65, 65], [event.ki.wVk for event in upper])
        self.assertEqual((0, 1), self.api.translation_calls[-1][2:4])
        lower = self.desktop.build_inputs("a", TARGET, "shift_enter")
        self.assertEqual([16, 65, 65, 16], [event.ki.wVk for event in lower])
        self.assertEqual((0x80, 1), self.api.translation_calls[-1][2:4])

    def test_ctrl_alt_layout_mapping_falls_back_to_unicode(self):
        self.api.mapping["a"] = 0x0641
        self.assertEqual(packets(unicode_inputs("a")), packets(self.desktop.build_inputs("a", TARGET, "enter")))
        self.assertEqual([], self.api.translation_calls)

    def test_unmapped_ascii_falls_back_to_unicode(self):
        self.api.mapping["a"] = -1
        self.assertEqual(packets(unicode_inputs("a")), packets(self.desktop.build_inputs("a", TARGET, "enter")))

    def test_dead_keys_and_mismatched_translations_fall_back_to_unicode(self):
        for count, translation in ((-1, "^"), (0, ""), (1, "b"), (2, "ab")):
            with self.subTest(count=count, translation=translation):
                self.api.translation_count = count
                self.api.translation_override = translation
                self.assertEqual(packets(unicode_inputs("a")), packets(self.desktop.build_inputs("a", TARGET, "enter")))

    def test_non_ascii_text_uses_unicode_without_keyboard_layout_mapping(self):
        self.assertEqual(packets(unicode_inputs("é")), packets(self.desktop.build_inputs("é", TARGET, "enter")))
        self.assertEqual([], self.api.mapping_calls)

    def test_focus_guard_prevents_calling_sendinput(self):
        self.api.foreground = 201
        with self.assertRaisesRegex(InputFailure, "Focus changed"):
            self.desktop.send_character("a", TARGET, "shift_enter")
        self.assertEqual([], self.api.send_calls)

    def test_sendinput_zero_produces_actionable_error_without_uncertainty(self):
        self.api.send_results.append(0)
        self.api.send_error = 5
        with self.assertRaisesRegex(InputFailure, "Windows blocked typing") as caught:
            self.desktop.send_character("a", TARGET, "shift_enter")
        self.assertFalse(caught.exception.uncertain)
        self.assertIn("Windows error 5", str(caught.exception))
        self.assertEqual(1, len(self.api.send_calls))

    def test_partial_shift_input_is_uncertain_and_releases_its_modifier(self):
        self.api.send_results.extend((1, 1))
        with self.assertRaisesRegex(InputFailure, "part of a keystroke") as caught:
            self.desktop.send_character("A", TARGET, "shift_enter")
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(2, len(self.api.send_calls))
        self.assertEqual((1, [(1, 16, 0, 2)], C.sizeof(INPUT)), self.api.send_calls[1])

    def test_partial_plain_input_does_not_release_unintroduced_modifiers(self):
        self.api.send_results.append(1)
        with self.assertRaises(InputFailure) as caught:
            self.desktop.send_character("a", TARGET, "enter")
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(1, len(self.api.send_calls))

    def test_success_requires_exact_event_count_and_native_structure_size(self):
        self.desktop.send_character("a", TARGET, "shift_enter")
        self.assertEqual((2, [(1, 65, 0x1E, 0), (1, 65, 0x1E, 2)], C.sizeof(INPUT)), self.api.send_calls[0])

    def test_capture_requires_real_external_focus_and_initializes_native_size(self):
        self.assertEqual(TARGET, self.desktop.capture_foreground())
        self.api.pid = self.desktop.pid
        self.assertIsNone(self.desktop.capture_foreground())
        self.api.pid = TARGET.process_id
        self.api.focus = 0
        self.assertIsNone(self.desktop.capture_foreground())
        self.api.focus = TARGET.focus
        self.api.window_class = "Shell_TrayWnd"
        self.assertIsNone(self.desktop.capture_foreground())

    def test_capture_refuses_failed_gui_queries_and_missing_threads(self):
        self.api.gui_ok = False
        self.assertIsNone(self.desktop.capture_foreground())
        self.api.gui_ok = True
        self.api.thread = 0
        self.assertIsNone(self.desktop.capture_foreground())

    def test_capture_refuses_missing_or_invisible_foreground_windows(self):
        self.api.foreground = 0
        self.assertIsNone(self.desktop.capture_foreground())
        self.api.foreground = TARGET.window
        self.api.visible = False
        self.assertIsNone(self.desktop.capture_foreground())

    def test_capture_refuses_foreground_changes_during_the_native_snapshot(self):
        self.api.foreground_answers.extend((TARGET.window, 201))
        self.assertIsNone(self.desktop.capture_foreground())

    def test_stale_focus_handle_or_process_identity_invalidates_target(self):
        self.assertTrue(self.desktop.is_valid(TARGET))
        self.api.valid_windows.remove(TARGET.focus)
        self.assertFalse(self.desktop.is_valid(TARGET))
        self.api.valid_windows.add(TARGET.focus)
        self.api.pid = 99
        self.assertFalse(self.desktop.is_valid(TARGET))

    def test_stale_thread_or_own_process_identity_invalidates_target(self):
        self.api.thread = 99
        self.assertFalse(self.desktop.is_valid(TARGET))
        self.api.thread = TARGET.thread_id
        self.api.pid = self.desktop.pid
        self.assertFalse(self.desktop.is_valid(TARGET))

    def test_focus_requires_live_matching_window_control_and_gui_query(self):
        self.assertTrue(self.desktop.has_focus(TARGET))
        self.api.gui_ok = False
        self.assertFalse(self.desktop.has_focus(TARGET))
        self.api.gui_ok = True
        self.api.focus = 202
        self.assertFalse(self.desktop.has_focus(TARGET))
        self.api.focus = TARGET.focus
        self.api.foreground = 201
        self.assertFalse(self.desktop.has_focus(TARGET))

    def test_activation_is_only_a_request_and_restores_minimized_windows(self):
        self.api.iconic = True
        self.assertFalse(self.desktop.activate(TARGET))
        self.assertEqual([(TARGET.window, 9)], self.api.show_requests)
        self.assertEqual([TARGET.window], self.api.activation_requests)

    def test_activation_does_nothing_for_a_stale_saved_window(self):
        self.api.valid_windows.remove(TARGET.window)
        self.api.iconic = True
        self.assertFalse(self.desktop.activate(TARGET))
        self.assertEqual([], self.api.show_requests)
        self.assertEqual([], self.api.activation_requests)

    def test_modifier_or_mouse_wait_checks_only_relevant_keys(self):
        self.assertFalse(self.desktop.modifiers_or_mouse_down())
        for key in (1, 2, 4, 16, 17, 18, 91, 92):
            with self.subTest(key=key):
                self.api.keys_down = {key}
                self.assertTrue(self.desktop.modifiers_or_mouse_down())
        self.api.keys_down = {65}
        self.assertFalse(self.desktop.modifiers_or_mouse_down())

    def test_clipboard_unicode_text_is_read_and_always_unlocked_and_closed(self):
        self.assertEqual("Clipboard café 🙂", self.desktop.read_clipboard_text())
        self.assertEqual([77], self.api.clipboard_unlocks)
        self.assertEqual(1, self.api.clipboard_closes)

    def test_clipboard_open_busy_retries_are_bounded(self):
        self.api.clipboard_open_answers.extend([False] * 6)
        with patch("win32_backend.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "clipboard is busy"):
                self.desktop.read_clipboard_text()
        self.assertEqual(6, self.api.clipboard_opens)
        self.assertEqual(5, sleep.call_count)
        self.assertEqual(0, self.api.clipboard_closes)

    def test_clipboard_format_handle_size_and_lock_errors_always_close(self):
        for failing_field, value in (("clipboard_has_text", False), ("clipboard_handle", 0),
                                     ("clipboard_size", 2_000_006), ("clipboard_lock_ok", False)):
            with self.subTest(field=failing_field):
                self.setUp()
                setattr(self.api, failing_field, value)
                with self.assertRaises(RuntimeError):
                    self.desktop.read_clipboard_text()
                self.assertEqual(1, self.api.clipboard_closes)
                self.assertEqual([], self.api.clipboard_unlocks)

    def test_clipboard_decode_error_still_unlocks_and_closes(self):
        with patch("win32_backend.C.wstring_at", side_effect=ValueError("bad data")):
            with self.assertRaisesRegex(ValueError, "bad data"):
                self.desktop.read_clipboard_text()
        self.assertEqual([77], self.api.clipboard_unlocks)
        self.assertEqual(1, self.api.clipboard_closes)


if __name__ == "__main__":
    unittest.main()
