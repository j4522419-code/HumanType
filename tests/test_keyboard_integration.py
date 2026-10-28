"""Keypress-count/guard integration. No real hook, desktop input, or clipboard."""
from __future__ import annotations
import unittest
from core import InputFailure, TypingController, TypingError
from keyboard_guard import KeyboardPolicy, LLKHF_INJECTED, WM_KEYDOWN, WM_KEYUP
from tests.test_core import Clock, FakeDesktop, FakeGuard, FIELD_A, FIELD_B


class RecordingDesktop(FakeDesktop):
    def __init__(self, clock, guard, trace):
        super().__init__(clock)
        self.guard, self.trace = guard, trace
    def send_character(self, character, target, newline_mode):
        self.trace.append(("send", character, target, self.guard.active_target))
        super().send_character(character, target, newline_mode)


class PolicyGuardAdapter:
    """Connect the real pure policy to the controller without a native hook."""
    def __init__(self, clock): self.policy = KeyboardPolicy(clock=clock)
    def activate(self, target): self.policy.activate(target)
    def deactivate(self): self.policy.deactivate()
    def consume_pause_request(self): return self.policy.consume_pause_request()
    def consume_keypresses(self): return self.policy.consume_keypresses()
    def close(self): self.policy.deactivate()


class KeyboardGuardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.clock = Clock()
        self.guard = FakeGuard(self.trace)
        self.desktop = RecordingDesktop(self.clock, self.guard, self.trace)
        self.controller = TypingController(self.desktop, clock=self.clock, keyboard_guard=self.guard)

    def at(self, time):
        self.clock.now = time
        self.controller.tick()
    def begin(self, text="abc"):
        self.controller.paste(text)
        self.controller.start()
    def arm(self, text="abc"):
        self.begin(text)
        self.at(3.0)
        self.assertEqual("typing", self.controller.state)
        self.assertEqual("", self.desktop.output)
    def press(self, count=1):
        self.guard.pending_count += count
        self.controller.tick()
    def activations(self): return [event for event in self.trace if event[0] == "activate"]

    def test_guard_stays_disabled_during_start_countdown(self):
        self.begin()
        for time in (0.0, 1.0, 2.999): self.at(time)
        self.assertEqual("countdown", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual([], self.activations())
        self.assertEqual("", self.desktop.output)

    def test_guard_is_activated_before_consuming_keypresses_or_sending(self):
        self.arm()
        self.trace.clear()
        self.press()
        actions = [event[0] for event in self.trace]
        self.assertLess(actions.index("activate"), actions.index("consume_keys"))
        self.assertLess(actions.index("consume_keys"), actions.index("send"))
        send = next(event for event in self.trace if event[0] == "send")
        self.assertEqual(FIELD_A, send[3])
        self.assertEqual("a", self.desktop.output)

    def test_guard_uses_the_manually_chosen_countdown_destination(self):
        self.begin()
        self.desktop.current = FIELD_B
        self.at(3.0)
        self.press()
        self.assertEqual(FIELD_B, self.guard.active_target)
        self.assertEqual(FIELD_B, self.desktop.sent[0][1])

    def test_guard_heartbeat_refreshes_while_waiting_for_physical_keypresses(self):
        self.arm()
        before = len(self.activations())
        self.at(100)
        self.assertGreater(len(self.activations()), before)
        self.assertEqual(FIELD_A, self.guard.active_target)
        self.assertEqual("", self.desktop.output)

    def test_guard_is_activated_even_when_no_keypresses_are_available(self):
        self.arm()
        self.assertEqual(FIELD_A, self.guard.active_target)
        self.controller.tick()
        self.assertEqual("", self.desktop.output)

    def test_explicit_pause_releases_guard_and_discards_pending_keys(self):
        self.arm("x" * 50)
        self.press(50)
        previous = self.desktop.output
        self.controller.stop()
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)
        self.controller.resume()
        self.at(6)
        self.assertEqual(previous, self.desktop.output)

    def test_start_pause_toggle_releases_the_keyboard_guard(self):
        self.arm()
        self.press()
        self.controller.start()
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)

    def test_clear_stops_the_guard_and_discards_remaining_input(self):
        self.arm()
        self.press()
        self.guard.pending_count = 20
        self.controller.clear()
        self.assertEqual("empty", self.controller.state)
        self.assertEqual("", self.controller.text)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)
        self.at(100)
        self.controller.resume()
        self.at(200)
        self.assertEqual("a", self.desktop.output)

    def test_completion_releases_guard_and_discards_extra_keypresses(self):
        self.arm("a")
        self.press(20)
        self.assertEqual("completed", self.controller.state)
        self.assertEqual("a", self.desktop.output)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)
        send = next(event for event in self.trace if event[0] == "send")
        self.assertEqual(FIELD_A, send[3])

    def test_focus_loss_releases_guard_and_discards_presses_before_sending(self):
        self.arm()
        self.press()
        self.guard.pending_count = 10
        self.desktop.current = FIELD_B
        self.at(4.0)
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)
        self.assertEqual("a", self.desktop.output)

    def test_input_failure_releases_guard_and_discards_presses_without_advancing(self):
        self.arm()
        self.desktop.failure = InputFailure("Windows blocked input")
        self.press(20)
        self.assertEqual("paused", self.controller.state)
        self.assertEqual(0, self.controller.position)
        self.assertEqual("", self.desktop.output)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)

    def test_partial_input_failure_releases_guard_and_prevents_retry(self):
        self.arm()
        self.desktop.failure = InputFailure("Partial input", uncertain=True)
        self.press()
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        with self.assertRaises(TypingError): self.controller.resume()
        self.assertEqual("", self.desktop.output)

    def test_guard_install_errors_pause_without_sending_any_input(self):
        for error in (RuntimeError("Hook unavailable"), OSError("Hook denied")):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.begin()
                self.guard.failure = error
                self.at(3.0)
                self.assertEqual("paused", self.controller.state)
                self.assertEqual("", self.desktop.output)
                self.assertIsNone(self.guard.active_target)
                self.assertTrue(self.controller.message)

    def test_guard_refresh_error_pauses_and_releases_an_already_active_guard(self):
        self.arm()
        self.press()
        self.guard.failure = RuntimeError("Guard thread exited")
        self.guard.pending_count = 10
        self.at(4.0)
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)
        self.assertEqual("a", self.desktop.output)

    def test_pause_request_wins_over_pending_physical_keypresses(self):
        self.arm()
        self.press()
        self.guard.pending_count = 10
        self.guard.pause_requested = True
        self.at(4.0)
        self.assertEqual("paused", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertFalse(self.guard.pause_requested)
        self.assertEqual(0, self.guard.pending_count)
        self.assertEqual("a", self.desktop.output)

    def test_resume_restores_guard_without_reusing_old_keypresses(self):
        self.arm()
        self.press()
        self.controller.stop()
        self.guard.pending_count = 10
        self.controller.resume()
        before = len(self.activations())
        self.at(4.0)
        self.at(5.999)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(before, len(self.activations()))
        self.at(6.0)
        self.assertEqual("a", self.desktop.output)
        self.assertEqual(FIELD_A, self.guard.active_target)
        self.press()
        self.assertEqual("ab", self.desktop.output)

    def test_idle_ticks_release_stale_guard_and_pending_keypresses(self):
        self.guard.active_target = FIELD_A
        self.guard.pending_count = 10
        self.at(0.0)
        self.assertEqual("empty", self.controller.state)
        self.assertIsNone(self.guard.active_target)
        self.assertEqual(0, self.guard.pending_count)

    def test_real_policy_turns_asdfg_into_hello_without_recursive_keypresses(self):
        clock = Clock()
        desktop = FakeDesktop(clock)
        guard = PolicyGuardAdapter(clock)
        controller = TypingController(desktop, clock=clock, keyboard_guard=guard)
        controller.paste("Hello")
        controller.start()
        clock.now = 3.0
        controller.tick()
        self.assertEqual("", desktop.output)
        for position, pressed in enumerate("asdfg", start=1):
            physical_vk = ord(pressed.upper())
            self.assertTrue(guard.policy.handle(physical_vk, 0, WM_KEYDOWN, FIELD_A.window, FIELD_A.focus))
            def injected_packets():
                clipboard_vk = ord(desktop.sent[-1][0].upper())
                self.assertFalse(guard.policy.handle(clipboard_vk, LLKHF_INJECTED, WM_KEYDOWN, FIELD_A.window, FIELD_A.focus))
                self.assertFalse(guard.policy.handle(clipboard_vk, LLKHF_INJECTED, WM_KEYUP, FIELD_A.window, FIELD_A.focus))
                # Even an injected up matching the physical trigger must not
                # remove the swallowed physical down's pairing information.
                self.assertFalse(guard.policy.handle(physical_vk, LLKHF_INJECTED, WM_KEYUP, FIELD_A.window, FIELD_A.focus))
            desktop.on_send = injected_packets
            controller.tick()
            self.assertEqual("Hello"[:position], desktop.output)
            self.assertTrue(guard.policy.handle(physical_vk, 0, WM_KEYUP, FIELD_A.window, FIELD_A.focus))
            controller.tick()
            self.assertEqual("Hello"[:position], desktop.output, "Injected text recursively triggered another clipboard character")
        self.assertEqual("completed", controller.state)
        self.assertFalse(guard.policy.active)


if __name__ == "__main__": unittest.main()
