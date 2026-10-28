"""Key-per-character controller regressions. All desktop and key events are fake."""
from __future__ import annotations
from collections import deque
import unittest
from core import InputFailure, Target, TypingController, TypingError, normalize_text

FIELD_A = Target(101, 102, 11, 12, "Editor A")
FIELD_B = Target(201, 202, 21, 22, "Editor B")


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class FakeGuard:
    def __init__(self, trace=None):
        self.trace = trace if trace is not None else []
        self.active_target = None
        self.failure = None
        self.pause_requested = False
        self.pending_count = 0
        self.closed = False

    def activate(self, target):
        self.trace.append(("activate", target))
        if self.failure: raise self.failure
        self.active_target = target

    def deactivate(self):
        self.trace.append(("deactivate",))
        self.active_target = None
        self.pending_count = 0

    def consume_pause_request(self):
        self.trace.append(("consume_pause",))
        requested, self.pause_requested = self.pause_requested, False
        return requested

    def consume_keypresses(self):
        self.trace.append(("consume_keys", self.pending_count))
        count, self.pending_count = self.pending_count, 0
        return count

    def close(self):
        self.deactivate()
        self.closed = True


class FakeDesktop:
    def __init__(self, clock):
        self.clock = clock
        self.current = FIELD_A
        self.valid_windows = {FIELD_A.window, FIELD_B.window}
        self.activation_requests = []
        self.focus_answers = deque()
        self.modifiers_down = False
        self.failure = None
        self.sent = []
        self.on_send = None

    def capture_foreground(self): return self.current
    def is_valid(self, target): return target is not None and target.window in self.valid_windows
    def activate(self, target):
        self.activation_requests.append(target)
        return False  # An activation request is not synchronous focus success.
    def has_focus(self, target):
        return self.focus_answers.popleft() if self.focus_answers else self.is_valid(target) and self.current == target
    def modifiers_or_mouse_down(self): return self.modifiers_down
    def send_character(self, character, target, newline_mode):
        if self.failure is not None: raise self.failure
        self.sent.append((character, target, newline_mode, self.clock()))
        if self.on_send: self.on_send()
    @property
    def output(self): return "".join(event[0] for event in self.sent)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.desktop = FakeDesktop(self.clock)
        self.guard = FakeGuard()
        self.controller = TypingController(self.desktop, clock=self.clock, keyboard_guard=self.guard)

    def at(self, time):
        self.clock.now = time
        self.controller.tick()

    def press(self, count=1):
        self.guard.pending_count += count
        self.controller.tick()

    def load_and_start(self, text="abcdef"):
        self.controller.paste(text)
        self.controller.start()

    def arm(self, text="abcdef"):
        self.load_and_start(text)
        self.at(self.clock.now + self.controller.countdown_seconds)
        self.assertEqual("typing", self.controller.state)
        self.assertEqual("", self.desktop.output)

    def finish(self):
        if self.controller.state == "countdown":
            self.at(self.clock.now + self.controller.countdown_seconds + 1)
        self.assertEqual("typing", self.controller.state)
        self.guard.pending_count += self.controller.total - self.controller.position
        for _ in range(200):
            if self.controller.state == "completed": break
            self.controller.tick()
        self.assertEqual("completed", self.controller.state)

    def test_paste_is_allowed_without_a_destination(self):
        self.desktop.current = None
        self.controller.paste("Clipboard first")
        self.assertEqual("ready", self.controller.state)
        self.assertEqual("Clipboard first", self.controller.text)
        self.assertIsNone(self.controller.target)
        self.assertIn("destination field", self.controller.message)

    def test_empty_control_only_or_oversized_clipboard_is_rejected(self):
        for text in ("", "\0\x01\x1b", "x" * 1_000_001):
            with self.subTest(length=len(text)), self.assertRaises(TypingError): self.controller.paste(text)
        self.assertEqual("empty", self.controller.state)

    def test_normalization_keeps_unicode_joiners_and_whitespace(self):
        source = "  café\r\ne\u0301\r\t你好🙂👩\u200d💻!\0\x01\x08\x1b\ud800  "
        expected = "  café\ne\u0301\n\t你好🙂👩\u200d💻!  "
        self.assertEqual(expected, normalize_text(source))
        self.controller.paste(source)
        self.assertEqual(expected, self.controller.text)
        self.assertEqual(len(expected), self.controller.total)

    def test_start_from_the_field_needs_no_focus_transfer(self):
        self.load_and_start()
        self.assertEqual("countdown", self.controller.state)
        self.assertEqual(FIELD_A, self.controller.target)
        self.assertEqual([], self.desktop.activation_requests)
        self.at(3.0)
        self.assertEqual("", self.desktop.output)
        self.press()
        self.assertEqual("a", self.desktop.output)

    def test_button_start_checks_activation_later_instead_of_failing_immediately(self):
        self.controller.paste("abc")
        self.desktop.current = None
        self.controller.start()
        self.assertEqual([FIELD_A], self.desktop.activation_requests)
        self.assertEqual("countdown", self.controller.state)
        self.at(1.0)
        self.desktop.current = FIELD_A
        self.at(3.0)
        self.assertEqual("typing", self.controller.state)
        self.assertEqual("", self.desktop.output)
        self.press()
        self.assertEqual("a", self.desktop.output)

    def test_user_can_choose_a_field_during_countdown(self):
        self.desktop.current = None
        self.load_and_start("abc")
        self.at(1.0)
        self.desktop.current = FIELD_B
        self.at(3.0)
        self.assertEqual(FIELD_B, self.controller.target)
        self.press()
        self.assertEqual(FIELD_B, self.desktop.sent[0][1])

    def test_elapsed_time_alone_never_types_clipboard_text(self):
        self.load_and_start()
        for time in (0, 1.0, 2.999, 3.0, 100.0, 10_000.0): self.at(time)
        self.assertEqual("typing", self.controller.state)
        self.assertEqual(0, self.controller.position)
        self.assertEqual("", self.desktop.output)

    def test_countdown_without_focus_explains_recovery_and_sends_nothing(self):
        self.desktop.current = None
        self.load_and_start("abc")
        self.at(3.0)
        self.assertEqual("countdown", self.controller.state)
        self.at(4.0)
        self.assertEqual("ready", self.controller.state)
        self.assertIn("text field", self.controller.message)
        self.assertEqual("", self.desktop.output)
        self.desktop.current = FIELD_A
        self.controller.start()
        self.finish()
        self.assertEqual("abc", self.desktop.output)

    def test_stop_before_first_character_can_resume_after_choosing_a_field(self):
        self.desktop.current = None
        self.load_and_start("abc")
        self.controller.stop()
        self.assertEqual("paused", self.controller.state)
        self.desktop.current = FIELD_A
        self.controller.resume()
        self.finish()
        self.assertEqual("abc", self.desktop.output)

    def test_stop_and_continue_preserve_exact_remaining_text(self):
        self.arm()
        self.press()
        self.controller.stop()
        self.at(100.0)
        self.assertEqual("a", self.desktop.output)
        self.controller.resume()
        self.at(103.0)
        self.assertEqual("a", self.desktop.output)
        self.finish()
        self.assertEqual("abcdef", self.desktop.output)

    def test_start_action_resumes_a_paused_run_without_restarting_text(self):
        self.arm("abcd")
        self.press()
        self.controller.stop()
        self.controller.start()
        self.finish()
        self.assertEqual("abcd", self.desktop.output)

    def test_start_action_toggles_countdown_to_paused_and_then_resumes(self):
        self.load_and_start("abcd")
        self.at(1.0)
        self.controller.start()
        self.assertEqual("paused", self.controller.state)
        self.at(10.0)
        self.assertEqual("", self.desktop.output)
        self.controller.start()
        self.finish()
        self.assertEqual("abcd", self.desktop.output)

    def test_start_action_toggles_typing_to_paused_without_restarting(self):
        self.arm("abcd")
        self.press()
        self.controller.start()
        self.assertEqual("paused", self.controller.state)
        self.at(10.0)
        self.assertEqual("a", self.desktop.output)
        self.controller.start()
        self.finish()
        self.assertEqual("abcd", self.desktop.output)

    def test_resume_keeps_the_original_destination_locked(self):
        self.arm("abc")
        self.press()
        self.controller.stop()
        self.desktop.current = FIELD_B
        self.at(4.0)
        self.assertEqual(FIELD_A, self.controller.target)
        self.controller.resume()
        self.at(8.0)
        self.assertEqual("paused", self.controller.state)
        self.assertEqual("a", self.desktop.output)
        self.desktop.current = FIELD_A
        self.controller.resume()
        self.finish()
        self.assertEqual("abc", self.desktop.output)
        self.assertTrue(all(event[1] == FIELD_A for event in self.desktop.sent))

    def test_focus_loss_pauses_and_discards_pending_keypresses(self):
        self.arm()
        self.press()
        self.guard.pending_count = 5
        self.desktop.current = FIELD_B
        self.at(4.0)
        self.assertEqual("paused", self.controller.state)
        self.assertEqual("a", self.desktop.output)
        self.assertEqual(0, self.guard.pending_count)
        self.desktop.current = FIELD_A
        self.controller.resume()
        self.at(7.0)
        self.assertEqual("a", self.desktop.output)

    def test_focus_is_rechecked_before_sending_a_buffered_character(self):
        self.arm()
        self.desktop.focus_answers.extend((True, False))
        self.press()
        self.assertEqual("paused", self.controller.state)
        self.assertEqual("", self.desktop.output)

    def test_focus_is_rechecked_between_characters_in_one_tick(self):
        self.arm()
        self.desktop.on_send = lambda: setattr(self.desktop, "current", FIELD_B)
        self.press(6)
        self.assertEqual("a", self.desktop.output)
        self.assertEqual("paused", self.controller.state)
        self.desktop.current = FIELD_A
        self.desktop.on_send = None
        self.controller.resume()
        self.at(6.0)
        self.assertEqual("a", self.desktop.output)

    def test_blocked_input_does_not_advance_progress_or_reuse_pending_presses(self):
        self.arm("abc")
        self.desktop.failure = InputFailure("Windows blocked input")
        self.press(3)
        self.assertEqual("paused", self.controller.state)
        self.assertEqual(0, self.controller.position)
        self.desktop.failure = None
        self.controller.resume()
        self.at(6.0)
        self.assertEqual("", self.desktop.output)
        self.finish()
        self.assertEqual("abc", self.desktop.output)

    def test_uncertain_partial_input_blocks_automatic_retry(self):
        self.arm("abc")
        self.desktop.failure = InputFailure("Partial input", uncertain=True)
        self.press(3)
        self.assertEqual("paused", self.controller.state)
        with self.assertRaisesRegex(TypingError, "part of a keystroke"): self.controller.resume()
        self.assertEqual(0, self.controller.position)
        self.desktop.failure = None
        self.controller.paste("remaining")
        self.controller.start()
        self.finish()
        self.assertEqual("remaining", self.desktop.output)

    def test_completion_cannot_replay_or_leak_surplus_presses_into_a_new_run(self):
        self.arm("a")
        self.press(20)
        self.assertEqual("completed", self.controller.state)
        self.assertEqual(0, self.guard.pending_count)
        self.controller.resume()
        self.at(100)
        with self.assertRaises(TypingError): self.controller.start()
        self.assertEqual("a", self.desktop.output)
        self.controller.paste("new")
        self.controller.start()
        self.at(103)
        self.assertEqual("a", self.desktop.output)
        self.press()
        self.assertEqual("an", self.desktop.output)

    def test_one_press_types_one_clipboard_codepoint_including_whitespace_and_emoji(self):
        sample = "A 🙂\n\t你"
        self.arm(sample)
        for position, char in enumerate(sample, start=1):
            self.press()
            self.assertEqual(sample[:position], self.desktop.output)
            self.assertEqual(position, self.controller.position)
            self.assertEqual(char, self.desktop.sent[-1][0])
        self.assertEqual("completed", self.controller.state)

    def test_multiple_keypresses_keep_clipboard_order_without_using_actual_key_values(self):
        self.arm("clipboard")
        self.press(3)
        self.assertEqual("cli", self.desktop.output)
        self.press(2)
        self.assertEqual("clipb", self.desktop.output)

    def test_pending_presses_are_processed_in_responsive_bounded_batches(self):
        sample = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        self.arm(sample)
        self.press(len(sample))
        self.assertGreater(self.controller.position, 0)
        self.assertLessEqual(self.controller.position, 16)
        self.assertEqual(sample[:self.controller.position], self.desktop.output)
        for _ in range(100):
            if self.controller.state == "completed": break
            before = self.controller.position
            self.controller.tick()
            self.assertLessEqual(self.controller.position - before, 16)
        self.assertEqual("completed", self.controller.state)
        self.assertEqual(sample, self.desktop.output)

    def test_pause_discards_buffered_surplus_and_resume_requires_new_presses(self):
        self.arm("x" * 50)
        self.press(50)
        previous = self.desktop.output
        self.controller.stop()
        self.controller.resume()
        self.at(6)
        self.at(100)
        self.assertEqual(previous, self.desktop.output)
        self.press()
        self.assertEqual(previous + "x", self.desktop.output)

    def test_keypresses_queued_while_paused_are_not_reused_after_resume(self):
        self.arm()
        self.press()
        self.controller.stop()
        self.guard.pending_count = 5
        self.controller.resume()
        self.at(6)
        self.assertEqual("a", self.desktop.output)

    def test_unicode_and_all_line_break_modes_reach_backend_unchanged(self):
        sample = "café e\u0301 你好🙂\n\t👩\u200d💻"
        for mode in ("shift_enter", "enter", "unicode"):
            with self.subTest(mode=mode):
                self.setUp()
                self.controller.newline_mode = mode
                self.load_and_start(sample)
                self.finish()
                self.assertEqual(sample, self.desktop.output)
                self.assertTrue(all(event[2] == mode for event in self.desktop.sent))

    def test_countdown_is_clamped_before_start(self):
        for seconds, expected in ((0, 1.0), (99, 10.0)):
            with self.subTest(seconds=seconds):
                self.setUp()
                self.controller.countdown_seconds = seconds
                self.load_and_start()
                self.assertEqual(expected, self.controller.countdown_seconds)

    def test_invalid_line_break_mode_is_rejected_before_typing(self):
        self.controller.paste("abc")
        self.controller.newline_mode = "invalid"
        with self.assertRaisesRegex(TypingError, "line-break"): self.controller.start()
        self.assertEqual("ready", self.controller.state)
        self.assertEqual("", self.desktop.output)

    def test_running_queue_cannot_be_replaced(self):
        self.load_and_start()
        with self.assertRaises(TypingError): self.controller.paste("replace")
        self.assertEqual("abcdef", self.controller.text)
        self.finish()
        self.assertEqual("abcdef", self.desktop.output)

    def test_clear_during_countdown_discards_text_and_pending_presses(self):
        self.load_and_start()
        self.guard.pending_count = 6
        self.controller.clear()
        self.assertEqual("empty", self.controller.state)
        self.assertEqual("", self.controller.text)
        self.assertEqual(0, self.controller.position)
        self.assertEqual(0, self.guard.pending_count)
        self.at(100)
        self.controller.resume()
        self.at(200)
        self.assertEqual("", self.desktop.output)

    def test_clear_during_typing_discards_surplus_without_later_sends(self):
        self.arm("x" * 50)
        self.press(50)
        previous = self.desktop.output
        self.controller.clear()
        self.assertEqual("empty", self.controller.state)
        self.assertEqual("", self.controller.text)
        self.assertEqual(0, self.controller.total)
        self.assertEqual(0, self.controller.position)
        self.assertIsNone(self.controller.target)
        self.at(100)
        self.controller.resume()
        self.at(200)
        self.assertEqual(previous, self.desktop.output)

    def test_stale_saved_window_prevents_resume(self):
        self.arm()
        self.press()
        self.controller.stop()
        self.desktop.valid_windows.remove(FIELD_A.window)
        with self.assertRaisesRegex(TypingError, "closed"): self.controller.resume()
        self.assertEqual("a", self.desktop.output)

    def test_missing_keyboard_guard_pauses_instead_of_typing_unfiltered(self):
        controller = TypingController(self.desktop, clock=self.clock)
        controller.paste("abc")
        controller.start()
        self.clock.now = 3.0
        controller.tick()
        self.assertEqual("paused", controller.state)
        self.assertEqual("", self.desktop.output)
        self.assertTrue(controller.message)


if __name__ == "__main__": unittest.main()
