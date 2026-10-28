"""Keyboard-filter tests. Every native function is fake; no hook is installed."""
from __future__ import annotations

import ctypes as C
import queue
import threading
import unittest

from core import Target
from keyboard_guard import (
    BLOCKED_KEYS, GUITHREADINFO, HOOKPROC, KBDLLHOOKSTRUCT, KeyboardGuard,
    KeyboardPolicy, LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, LLKHF_UP,
    MSG, SYSTEM_KEYS, TRIGGER_KEYS, WM_KEYDOWN, WM_KEYUP, WM_QUIT, WM_SYSKEYDOWN,
)

TARGET = Target(101, 102, 11, 12, "Editor")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.0
        self.policy = KeyboardPolicy(clock=lambda: self.now)

    def event(self, vk, *, flags=0, message=WM_KEYDOWN, window=101, focus=102):
        return self.policy.handle(vk, flags, message, window, focus)

    def test_inactive_passes_all_typing_keys(self):
        for key in BLOCKED_KEYS:
            self.assertFalse(self.event(key))

    def test_active_filters_letters_space_and_all_editing_keys(self):
        self.policy.activate(TARGET)
        for key in BLOCKED_KEYS:
            with self.subTest(key=key):
                self.assertTrue(self.event(key))

    def test_shift_stays_available_without_requesting_pause(self):
        self.policy.activate(TARGET)
        for key in (16, 0xA0, 0xA1):
            self.assertFalse(self.event(key))
        self.assertTrue(self.policy.active)
        self.assertFalse(self.policy.consume_pause_request())

    def test_injected_events_always_pass_and_do_not_break_physical_pair(self):
        self.policy.activate(TARGET)
        self.assertTrue(self.event(0x41))
        for flags in (LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, LLKHF_INJECTED | LLKHF_UP):
            self.assertFalse(self.event(0x41, flags=flags, message=WM_KEYUP))
        self.assertTrue(self.event(0x41, message=WM_KEYUP))
        self.assertFalse(self.event(0x41, message=WM_KEYUP))

    def test_key_repeats_are_filtered_and_one_up_releases_pair(self):
        self.policy.activate(TARGET)
        self.assertTrue(self.event(0x20))
        self.assertTrue(self.event(0x20))
        self.assertTrue(self.event(0x20, flags=LLKHF_UP))
        self.policy.deactivate()
        self.assertFalse(self.event(0x20, message=WM_KEYUP))

    def test_deactivate_restores_new_keydowns_but_swallows_paired_up(self):
        self.policy.activate(TARGET)
        self.assertTrue(self.event(0x41))
        self.policy.deactivate()
        self.assertFalse(self.event(0x42))
        self.assertTrue(self.event(0x41, message=WM_KEYUP))
        self.assertFalse(self.policy.active)

    def test_held_blocked_key_repeats_stay_paired_after_deactivation(self):
        self.policy.activate(TARGET)
        self.assertTrue(self.event(0x41))
        self.policy.deactivate()
        self.assertTrue(self.event(0x41))
        self.assertTrue(self.event(0x41))
        self.assertFalse(self.event(0x42))
        self.assertTrue(self.event(0x41, message=WM_KEYUP))
        self.assertFalse(self.event(0x41))

    def test_foreground_and_control_changes_release_keys_and_request_pause(self):
        for arguments in ({"window": 999}, {"focus": 999}):
            with self.subTest(arguments=arguments):
                self.policy = KeyboardPolicy(clock=lambda: self.now)
                self.policy.activate(TARGET)
                self.assertFalse(self.event(0x41, **arguments))
                self.assertFalse(self.policy.active)
                self.assertTrue(self.policy.consume_pause_request())
                self.assertFalse(self.policy.consume_pause_request())

    def test_expired_heartbeat_releases_input_and_requires_pause(self):
        self.policy.activate(TARGET)
        self.now += 0.501
        self.assertFalse(self.event(0x41))
        self.assertFalse(self.policy.active)
        self.assertTrue(self.policy.consume_pause_request())

    def test_activation_refreshes_heartbeat(self):
        self.policy.activate(TARGET)
        self.now += 0.4
        self.policy.activate(TARGET)
        self.now += 0.4
        self.assertTrue(self.event(0x41))

    def test_expired_lease_cannot_silently_reactivate(self):
        self.policy.activate(TARGET)
        self.now += 0.501
        with self.assertRaisesRegex(RuntimeError, "released"):
            self.policy.activate(TARGET)
        self.assertFalse(self.policy.active)
        self.assertTrue(self.policy.consume_pause_request())

    def test_escape_is_consumed_and_pauses_immediately(self):
        self.policy.activate(TARGET)
        self.assertTrue(self.event(27))
        self.assertFalse(self.policy.active)
        self.assertTrue(self.policy.consume_pause_request())
        self.assertTrue(self.event(27, message=WM_KEYUP))

    def test_system_modifier_and_function_keys_pass_and_pause(self):
        for key in SYSTEM_KEYS:
            with self.subTest(key=key):
                policy = KeyboardPolicy(clock=lambda: self.now)
                policy.activate(TARGET)
                self.assertFalse(policy.handle(key, 0, WM_SYSKEYDOWN, 101, 102))
                self.assertFalse(policy.active)
                self.assertTrue(policy.consume_pause_request())

    def test_pending_pause_prevents_activation_until_consumed(self):
        self.policy.request_pause()
        with self.assertRaises(RuntimeError):
            self.policy.activate(TARGET)
        self.assertTrue(self.policy.consume_pause_request())
        self.policy.activate(TARGET)
        self.assertTrue(self.policy.active)

    def test_unknown_messages_and_unpaired_keyups_pass(self):
        self.policy.activate(TARGET)
        self.assertFalse(self.event(0x41, message=123))
        self.assertFalse(self.event(0x41, message=WM_KEYUP))

    def test_each_initial_letter_or_space_press_enqueues_one_trigger(self):
        self.policy.activate(TARGET)
        for key in TRIGGER_KEYS:
            self.assertTrue(self.event(key))
        self.assertEqual(27, self.policy.consume_keypresses())
        self.assertEqual(0, self.policy.consume_keypresses())

    def test_held_key_repeat_never_enqueues_additional_triggers(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.event(0x41)
        self.event(0x41)
        self.assertEqual(1, self.policy.consume_keypresses())
        self.event(0x41)
        self.assertEqual(0, self.policy.consume_keypresses())
        self.event(0x41, message=WM_KEYUP)
        self.event(0x41)
        self.assertEqual(1, self.policy.consume_keypresses())

    def test_overlapping_physical_presses_count_independently(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.event(0x42)
        self.event(0x41)
        self.event(0x20)
        self.event(0x42, message=WM_KEYUP)
        self.event(0x42)
        self.assertEqual(4, self.policy.consume_keypresses())

    def test_injected_events_do_not_enqueue_or_release_physical_trigger(self):
        self.policy.activate(TARGET)
        self.event(0x41, flags=LLKHF_INJECTED)
        self.event(0x20, flags=LLKHF_LOWER_IL_INJECTED)
        self.assertEqual(0, self.policy.consume_keypresses())
        self.event(0x41)
        self.event(0x41, flags=LLKHF_INJECTED, message=WM_KEYUP)
        self.event(0x41)
        self.assertEqual(1, self.policy.consume_keypresses())

    def test_other_suppressed_editing_keys_never_advance_text(self):
        self.policy.activate(TARGET)
        for key in BLOCKED_KEYS - TRIGGER_KEYS:
            self.assertTrue(self.event(key))
        self.assertEqual(0, self.policy.consume_keypresses())

    def test_heartbeat_refresh_preserves_pending_triggers(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.now += 0.2
        self.policy.activate(TARGET)
        self.assertEqual(1, self.policy.consume_keypresses())

    def test_deactivate_discards_pending_presses_across_resume(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.policy.deactivate()
        self.assertEqual(0, self.policy.consume_keypresses())
        self.policy.activate(TARGET)
        self.assertEqual(0, self.policy.consume_keypresses())
        self.event(0x41)  # Still held from before Pause: no new press.
        self.assertEqual(0, self.policy.consume_keypresses())
        self.event(0x41, message=WM_KEYUP)
        self.event(0x41)
        self.assertEqual(1, self.policy.consume_keypresses())

    def test_pause_request_discards_pending_presses(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.policy.request_pause()
        self.assertEqual(0, self.policy.consume_keypresses())

    def test_escape_and_system_keys_discard_pending_presses(self):
        for key in (27, 17, 18, 91, 0x70):
            with self.subTest(key=key):
                policy = KeyboardPolicy(clock=lambda: self.now)
                policy.activate(TARGET)
                policy.handle(0x41, 0, WM_KEYDOWN, 101, 102)
                policy.handle(key, 0, WM_KEYDOWN, 101, 102)
                self.assertEqual(0, policy.consume_keypresses())
                self.assertTrue(policy.consume_pause_request())

    def test_scope_loss_discards_presses_even_on_held_repeat_or_keyup(self):
        for message in (WM_KEYDOWN, WM_KEYUP):
            with self.subTest(message=message):
                policy = KeyboardPolicy(clock=lambda: self.now)
                policy.activate(TARGET)
                policy.handle(0x41, 0, WM_KEYDOWN, 101, 102)
                policy.handle(0x41, 0, message, 999, 999)
                self.assertEqual(0, policy.consume_keypresses())
                self.assertTrue(policy.consume_pause_request())

    def test_expired_lease_discards_presses_without_new_keyboard_event(self):
        self.policy.activate(TARGET)
        self.event(0x41)
        self.now += 0.501
        self.assertEqual(0, self.policy.consume_keypresses())
        self.assertTrue(self.policy.consume_pause_request())


class FakeAPI:
    def __init__(self):
        self.messages = queue.Queue()
        self.window = TARGET.window
        self.focus = TARGET.focus
        self.gui_ok = True
        self.hook_result = 12345
        self.next_result = 73
        self.calls = []
        self.callback = None
        self.throw_snapshot = False

    def GetCurrentThreadId(self):
        return 778

    def PeekMessageW(self, *args):
        self.calls.append("queue")
        return 0

    def GetModuleHandleW(self, name):
        return 55

    def SetWindowsHookExW(self, hook, callback, module, thread):
        self.calls.append(("install", hook, module, thread))
        self.callback = callback
        return self.hook_result

    def GetMessageW(self, pointer, *args):
        value = self.messages.get(timeout=2)
        if isinstance(value, Exception):
            raise value
        return value

    def UnhookWindowsHookEx(self, handle):
        self.calls.append(("unhook", handle))
        return True

    def PostThreadMessageW(self, thread, message, wparam, lparam):
        self.calls.append(("post", thread, message))
        self.messages.put(0)
        return True

    def CallNextHookEx(self, hook, code, message, pointer):
        self.calls.append(("next", code, message))
        return self.next_result

    def GetForegroundWindow(self):
        if self.throw_snapshot:
            raise RuntimeError("Fake snapshot failure")
        return self.window

    def GetGUIThreadInfo(self, thread, pointer):
        info = C.cast(pointer, C.POINTER(GUITHREADINFO)).contents
        if info.cbSize != C.sizeof(GUITHREADINFO):
            raise AssertionError("Native size was not set")
        info.hwndFocus = self.focus
        return self.gui_ok


class GuardBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.guard = KeyboardGuard(api=self.api, clock=lambda: 1.0)

    def tearDown(self):
        self.guard.close()

    def event(self, vk, *, flags=0, message=WM_KEYDOWN, code=0):
        event = KBDLLHOOKSTRUCT(vk, 0, flags, 0, 0)
        return self.guard._hook_callback(code, message, C.addressof(event))

    def test_construction_is_lazy_and_installs_nothing(self):
        self.assertEqual([], self.api.calls)
        self.assertIsNone(self.guard._thread)

    def test_activation_installs_fake_hook_and_close_unhooks_it(self):
        self.guard.activate(TARGET)
        self.assertTrue(self.guard.active)
        self.assertIn(("install", 13, 55, 0), self.api.calls)
        self.assertIsNotNone(self.guard._callback)
        self.guard.close()
        self.assertFalse(self.guard.active)
        self.assertIn(("post", 778, WM_QUIT), self.api.calls)
        self.assertIn(("unhook", 12345), self.api.calls)

    def test_install_failure_raises_without_blocking_any_input(self):
        self.api.hook_result = 0
        with self.assertRaisesRegex(RuntimeError, "could not enable"):
            self.guard.activate(TARGET)
        self.assertFalse(self.guard.active)
        self.assertEqual(self.api.next_result, self.event(0x41))

    def test_negative_code_does_not_dereference_event_pointer(self):
        self.guard.policy.activate(TARGET)
        result = self.guard._hook_callback(-1, WM_KEYDOWN, 0)
        self.assertEqual(self.api.next_result, result)
        self.assertTrue(self.guard.active)

    def test_callback_suppresses_physical_but_passes_injected_keys(self):
        self.guard.policy.activate(TARGET)
        self.assertEqual(1, self.event(0x41))
        self.assertEqual(self.api.next_result, self.event(0x41, flags=LLKHF_INJECTED))

    def test_callback_exception_fails_open_and_requests_pause(self):
        self.guard.policy.activate(TARGET)
        self.api.throw_snapshot = True
        self.assertEqual(self.api.next_result, self.event(0x41))
        self.assertFalse(self.guard.active)
        self.assertTrue(self.guard.consume_pause_request())

    def test_failed_gui_snapshot_releases_filter(self):
        self.guard.policy.activate(TARGET)
        self.api.gui_ok = False
        self.assertEqual(self.api.next_result, self.event(0x41))
        self.assertFalse(self.guard.active)
        self.assertTrue(self.guard.consume_pause_request())

    def test_closed_guard_cannot_be_reactivated(self):
        self.guard.policy.activate(TARGET)
        self.guard.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.guard.activate(TARGET)
        self.assertEqual(self.api.next_result, self.event(0x41))

    def test_guard_exposes_draining_keypress_counter(self):
        self.guard.policy.activate(TARGET)
        self.assertEqual(1, self.event(0x41))
        self.assertEqual(1, self.guard.consume_keypresses())
        self.assertEqual(0, self.guard.consume_keypresses())

    def test_close_discards_queued_physical_keypresses(self):
        self.guard.policy.activate(TARGET)
        self.event(0x41)
        self.guard.close()
        self.assertEqual(0, self.guard.consume_keypresses())

    def test_native_event_shape_uses_pointer_sized_extra_info(self):
        self.assertEqual(24 if C.sizeof(C.c_void_p) == 8 else 20, C.sizeof(KBDLLHOOKSTRUCT))
        self.assertEqual(16, KBDLLHOOKSTRUCT.dwExtraInfo.offset)
        self.assertEqual(C.sizeof(C.c_void_p), C.sizeof(HOOKPROC))


if __name__ == "__main__":
    unittest.main()
