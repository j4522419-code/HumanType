"""Editable-draft checks with fake Tk widgets; no window or input is created."""
from __future__ import annotations

import unittest

from app import HumanTypeApp
from core import Target, TypingController


class FakeWidget:
    def __init__(self):
        self.options = {}
        self.visible = False

    def configure(self, **options):
        self.options.update(options)

    def place(self, **_options):
        self.visible = True

    def place_forget(self):
        self.visible = False


class FakeText(FakeWidget):
    def __init__(self, text=""):
        super().__init__()
        self.text = text
        self.modified = False
        self.delete_calls = 0
        self.reset_calls = 0
        self.focused = False

    def get(self, first, last):
        assert (first, last) == ("1.0", "end-1c")
        return self.text

    def delete(self, _first, _last):
        self.text = ""
        self.modified = True
        self.delete_calls += 1

    def insert(self, _index, text):
        self.text = text
        self.modified = True

    def edit_modified(self, value=None):
        if value is not None:
            self.modified = value
        return self.modified

    def edit_reset(self):
        self.reset_calls += 1

    def focus_set(self):
        self.focused = True


class FakeCanvas:
    def winfo_width(self):
        return 240

    def winfo_height(self):
        return 5

    def delete(self, _tag):
        pass

    def create_rectangle(self, *_args, **_kwargs):
        pass


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeRoot:
    def after(self, _milliseconds, _callback):
        return "scheduled"


class FakeDesktop:
    def __init__(self):
        self.clipboard_text = "clipboard replacement"
        self.clipboard_reads = 0

    def capture_foreground(self):
        return None

    def is_valid(self, _target):
        return True

    def activate(self, _target):
        return True

    def read_clipboard_text(self):
        self.clipboard_reads += 1
        return self.clipboard_text


def make_app(*, queued="", draft=None, paused=False):
    desktop = FakeDesktop()
    controller = TypingController(desktop, clock=lambda: 0.0)
    if queued:
        controller.paste(queued)
    if paused:
        controller.state = "paused"
        controller.position = 2
        controller.target = Target(10, 11, 12, 13, "Saved field")

    app = HumanTypeApp.__new__(HumanTypeApp)
    app.root = FakeRoot()
    app.controller = controller
    app.desktop = desktop
    app._closed = False
    app._after_id = None
    app._editor_text = controller.text if draft is None else draft
    app._syncing_editor = False
    app._editor_focused = False
    app._error = None
    app._error_baseline = None
    app._last_running = None
    app._primary_button_action = "start"
    app._primary_button_intent = None
    app.preview = FakeText(app._editor_text)
    app.placeholder = FakeWidget()
    for name in (
        "paste_button", "clear_button", "start_button", "continue_button",
        "countdown_spin", "newline_combo", "target_label", "progress_label",
        "percent_label", "status_label",
    ):
        setattr(app, name, FakeWidget())
    app.progress_canvas = FakeCanvas()
    app.countdown_var = FakeVar("3")
    app.newline_var = FakeVar("Shift+Enter")
    app._update_placeholder()
    app._render()
    return app


def edit(app, text):
    app.preview.text = text
    app.preview.modified = True
    app._on_editor_modified()


class TextEditorTests(unittest.TestCase):
    def test_placeholder_is_overlay_and_never_queued(self):
        app = make_app()
        self.assertTrue(app.placeholder.visible)
        self.assertEqual(app.preview.text, "")
        app.start()
        self.assertEqual(app.controller.text, "")
        self.assertEqual(app.controller.state, "empty")
        self.assertIn("Enter or paste", app._error)
        app._focus_editor()
        self.assertTrue(app.preview.focused)
        self.assertFalse(app.placeholder.visible)
        app._on_editor_focus_out()
        self.assertTrue(app.placeholder.visible)

    def test_typed_draft_enables_start_without_reading_clipboard(self):
        app = make_app()
        edit(app, "directly entered text")
        self.assertEqual(app.start_button.options["state"], "normal")
        self.assertEqual(app.controller.text, "")
        self.assertFalse(app.placeholder.visible)
        app.start()
        self.assertEqual(app.controller.text, "directly entered text")
        self.assertEqual(app.controller.state, "countdown")
        self.assertEqual(app.desktop.clipboard_reads, 0)

    def test_tick_never_overwrites_an_uncommitted_draft(self):
        app = make_app(queued="original text", draft="my unsaved edit")
        for _ in range(20):
            app._tick()
        self.assertEqual(app.preview.text, "my unsaved edit")
        self.assertEqual(app.preview.delete_calls, 0)
        self.assertEqual(app.controller.text, "original text")
        self.assertEqual(app.desktop.clipboard_reads, 0)

    def test_action_reads_editor_even_before_modified_event_arrives(self):
        app = make_app(queued="old")
        app.preview.text = "new value before event"
        app.start()
        self.assertEqual(app.controller.text, "new value before event")
        self.assertEqual(app.controller.state, "countdown")

    def test_unchanged_paused_text_resumes_at_saved_position(self):
        app = make_app(queued="abcdef", paused=True)
        original_target = app.controller.target
        self.assertEqual(app.start_button.options["text"], "Resume")
        self.assertEqual(app.continue_button.options["state"], "normal")
        app.start()
        self.assertEqual(app.controller.position, 2)
        self.assertEqual(app.controller.state, "countdown")
        self.assertEqual(app.controller.target, original_target)

    def test_edited_paused_text_starts_a_fresh_queue(self):
        app = make_app(queued="abcdef", paused=True)
        edit(app, "replacement")
        self.assertEqual(app.start_button.options["text"], "Start")
        self.assertEqual(app.continue_button.options["state"], "disabled")
        self.assertEqual(app.progress_label.options["text"], "0 / 11 characters")
        app.start()
        self.assertEqual(app.controller.text, "replacement")
        self.assertEqual(app.controller.position, 0)
        self.assertEqual(app.controller.state, "countdown")

    def test_continue_cannot_resume_a_changed_draft(self):
        app = make_app(queued="abcdef", paused=True, draft="replacement")
        app.resume()
        self.assertEqual(app.controller.state, "paused")
        self.assertEqual(app.controller.position, 2)
        self.assertEqual(app.controller.text, "abcdef")
        self.assertIn("text changed", app._error)

    def test_editing_back_to_original_restores_resume(self):
        app = make_app(queued="abcdef", paused=True)
        edit(app, "replacement")
        edit(app, "abcdef")
        self.assertEqual(app.start_button.options["text"], "Resume")
        self.assertEqual(app.continue_button.options["state"], "normal")
        app.resume()
        self.assertEqual(app.controller.position, 2)
        self.assertEqual(app.controller.state, "countdown")

    def test_blank_draft_disables_start_and_continue(self):
        app = make_app(queued="abcdef", paused=True)
        edit(app, "")
        self.assertEqual(app.start_button.options["state"], "disabled")
        self.assertEqual(app.continue_button.options["state"], "disabled")
        self.assertEqual(app.clear_button.options["state"], "normal")
        self.assertTrue(app.placeholder.visible)

    def test_paste_button_explicitly_replaces_draft_and_queue(self):
        app = make_app(draft="user draft")
        app.desktop.clipboard_text = "clipboard\r\ntext"
        app.paste()
        self.assertEqual(app.desktop.clipboard_reads, 1)
        self.assertEqual(app.preview.text, "clipboard\ntext")
        self.assertEqual(app.controller.text, "clipboard\ntext")
        self.assertEqual(app.start_button.options["state"], "normal")

    def test_clear_discards_both_draft_and_running_queue(self):
        app = make_app(queued="abcdef")
        app.controller.state = "typing"
        app._render()
        self.assertEqual(app.preview.options["state"], "disabled")
        app.clear()
        self.assertEqual(app.controller.state, "empty")
        self.assertEqual(app.controller.text, "")
        self.assertEqual(app.preview.text, "")
        self.assertEqual(app.preview.options["state"], "normal")
        self.assertEqual(app.preview.reset_calls, 1)
        self.assertTrue(app.placeholder.visible)

    def test_editor_is_readonly_only_while_counting_down_or_typing(self):
        app = make_app(queued="abcdef")
        for state in ("countdown", "typing", "paused", "completed"):
            app.controller.state = state
            app._render()
            expected = "disabled" if state in {"countdown", "typing"} else "normal"
            self.assertEqual(app.preview.options["state"], expected)

    def test_literal_hint_can_be_typed_as_real_content(self):
        app = make_app()
        edit(app, "paste text here")
        self.assertFalse(app.placeholder.visible)
        app.start()
        self.assertEqual(app.controller.text, "paste text here")

    def test_normalization_is_synced_once_when_start_commits_text(self):
        app = make_app(draft="one\r\ntwo\0")
        app.start()
        self.assertEqual(app.controller.text, "one\ntwo")
        self.assertEqual(app.preview.text, "one\ntwo")
        deletes = app.preview.delete_calls
        app.controller.stop()
        for _ in range(5):
            app._tick()
        self.assertEqual(app.preview.delete_calls, deletes)
        self.assertEqual(app.start_button.options["text"], "Resume")


if __name__ == "__main__":
    unittest.main()
