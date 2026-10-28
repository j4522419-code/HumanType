"""Deterministic typing state machine. No GUI, clipboard, or native side effects."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
import unicodedata


class TypingError(RuntimeError):
    """An actionable problem that the UI can display without crashing."""


class InputFailure(TypingError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class Target:
    window: int
    focus: int
    process_id: int
    thread_id: int
    title: str


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Preserve Unicode format characters (including emoji joiners) and whitespace.
    return "".join(c for c in text if (unicodedata.category(c) != "Cc" or c in "\n\t")
                   and not 0xD800 <= ord(c) <= 0xDFFF)


class TypingController:
    def __init__(self, desktop, *, clock=time.monotonic, keyboard_guard=None):
        self.desktop = desktop
        self.keyboard_guard = keyboard_guard
        self.clock = clock
        self.text = ""
        self.state = "empty"
        self.position = 0
        self.countdown_seconds = 3.0
        self.newline_mode = "shift_enter"
        self.message = "Paste text, press Start, then click your field during the countdown."
        self.target: Target | None = None
        self.candidate: Target | None = None
        self._pending_presses = 0
        self._deadline = 0.0
        self._focus_timeout = 0.0
        self._resuming = False
        self._uncertain = False

    @property
    def total(self) -> int:
        return len(self.text)

    @property
    def target_name(self) -> str:
        destination = self.target or self.candidate
        return destination.title if destination else "Choose a field during the countdown"

    def _capture(self):
        found = self.desktop.capture_foreground()
        if found and self.desktop.is_valid(found):
            self.candidate = found
            return found
        return None

    def paste(self, text: str) -> None:
        self._require_idle()
        if len(text) > 1_000_000:
            raise TypingError("Copy less than 1 million characters at a time.")
        normalized = normalize_text(text)
        if not normalized:
            raise TypingError("The clipboard has no text. Copy some text and press Paste.")
        self._release_keyboard()
        self._capture()
        self.text = normalized
        self.position = 0
        self.target = None
        self._uncertain = False
        self.state = "ready"
        self.message = "Ready. Press Start, then click your destination field during the countdown."

    def start(self) -> None:
        if self.state in {"countdown", "typing"}:
            self.stop("Paused. Use Start / Pause again to resume the remaining text.")
            return
        if self.state == "paused":
            self.resume()
            return
        if self.state != "ready":
            raise TypingError("Press Paste to load text before starting.")
        self._begin(resuming=False)

    def resume(self) -> None:
        if self.state != "paused" or self.position >= self.total:
            return
        if self._uncertain:
            raise TypingError("Windows accepted only part of a keystroke. Check the destination and Paste the remaining text before starting again.")
        self._begin(resuming=self.position > 0 or self.target is not None)

    def _begin(self, *, resuming: bool) -> None:
        self.countdown_seconds = max(1.0, min(10.0, float(self.countdown_seconds)))
        if self.newline_mode not in {"shift_enter", "enter", "unicode"}:
            raise TypingError("Choose a valid line-break mode.")
        self._release_keyboard()
        current = self._capture()
        if resuming:
            if not self.target or not self.desktop.is_valid(self.target):
                raise TypingError("The saved window is closed. Paste again and choose a new field.")
        else:
            self.target = current or (self.candidate if self.candidate and self.desktop.is_valid(self.candidate) else None)
        # A button start requests activation; success is checked asynchronously.
        if self.target and current is None:
            self.desktop.activate(self.target)
        self._resuming = resuming
        self._deadline = self.clock() + self.countdown_seconds
        self._focus_timeout = self._deadline + 1.0
        self.state = "countdown"
        self.message = f"Starting in {math.ceil(self.countdown_seconds)} seconds. Click your field now."

    def stop(self, reason="Stopped. Continue resumes from the next character.") -> None:
        self._release_keyboard()
        if self.state in {"countdown", "typing"}:
            self.state = "paused"
            self.message = reason

    def clear(self) -> None:
        self.stop()
        self.text = ""
        self.position = 0
        self.target = None
        self._uncertain = False
        self.state = "empty"
        self.message = "Cleared. Paste text, press Start, then click your field during the countdown."

    def tick(self) -> None:
        if self.keyboard_guard and self.keyboard_guard.consume_pause_request():
            self.stop("Paused. Normal keyboard input is restored; Continue resumes the remaining text.")
            return
        if self.state not in {"countdown", "typing"}:
            self._release_keyboard()
            self._capture()
            return
        current_time = self.clock()
        if self.state == "countdown":
            current = self._capture()
            if current_time < self._deadline:
                self.message = f"Starting in {math.ceil(self._deadline - current_time)} seconds. Click your field now."
                return
            if not self._resuming and current:
                # The user's last click during the countdown explicitly chooses the field.
                self.target = current
            if self.target and self.desktop.has_focus(self.target):
                self.state = "typing"
            elif current_time < self._focus_timeout:
                self.message = "Waiting for the destination. Click your field now, or use Clear to cancel."
                return
            else:
                self.stop("No destination is focused. Press Start and click your text field during the countdown.")
                # If nothing has been typed, a retry may choose a fresh destination.
                if self.position == 0:
                    self.target = None
                    self.state = "ready"
                return
        if not self.target or not self.desktop.has_focus(self.target):
            self.stop("Focus changed. Continue returns to the saved field after the countdown.")
            return
        if self.keyboard_guard is None:
            self.stop("Keyboard input capture is unavailable. Restart the app before trying again.")
            return
        try:
            # Keep the filter ready while waiting for the next real keypress.
            self.keyboard_guard.activate(self.target)
        except (RuntimeError, OSError) as error:
            self.stop(f"Could not capture physical typing keys: {error}. Normal keyboard input is restored.")
            return
        # Wait for a mouse click or Ctrl/Alt/Shift to be released instead of stopping
        # immediately after a Start click or a layout-generated Shift key.
        if self.desktop.modifiers_or_mouse_down():
            self.message = "Release the mouse and modifier keys to continue. Clear cancels the queue."
            return
        self._pending_presses += self.keyboard_guard.consume_keypresses()
        # A bounded drain handles fast separate presses without tying pacing to a
        # timer, while leaving the UI responsive if many presses arrive together.
        for _ in range(min(16, self._pending_presses)):
            if self.keyboard_guard.consume_pause_request():
                self.stop("Paused. Normal keyboard input is restored; Continue resumes the remaining text.")
                return
            if not self.desktop.has_focus(self.target):
                self.stop("Focus changed. Continue returns to the saved field after the countdown.")
                return
            char = self.text[self.position]
            try:
                # Backend independently rechecks focus before sending one character.
                self.desktop.send_character(char, self.target, self.newline_mode)
            except InputFailure as error:
                self._uncertain = error.uncertain
                self.stop(str(error))
                return
            self._pending_presses -= 1
            self.position += 1
            if self.position == self.total:
                self.state = "completed"
                self._release_keyboard()
                self.message = "Finished. Normal typing is restored. Paste new text for another run."
                return
        self.message = "Ready. Each letter or Space types the next pasted character. Escape pauses."

    def _require_idle(self) -> None:
        if self.state in {"countdown", "typing"}:
            raise TypingError("Pause before replacing the text, or use Clear to discard it.")

    def _release_keyboard(self) -> None:
        self._pending_presses = 0
        if self.keyboard_guard:
            self.keyboard_guard.deactivate()
