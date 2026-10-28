"""Black-and-white Tk interface for HumanType.

Importing this module never creates a window.  The native desktop boundary and
typing state machine are supplied by main.py so they can be tested separately.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from core import TypingController, TypingError
from win32_backend import WindowsDesktop


class HumanTypeApp:
    BACKGROUND = "#0d0d0d"
    SURFACE = "#1c1c1c"
    BORDER = "#434343"
    FOREGROUND = "#f4f4f4"
    MUTED = "#ababab"
    WHITE = "#ffffff"

    NEWLINE_MODES = {
        "Shift+Enter": "shift_enter",
        "Enter": "enter",
        "Text only": "unicode",
    }

    def __init__(
        self,
        root: tk.Tk,
        controller: TypingController,
        desktop: WindowsDesktop,
    ) -> None:
        self.root = root
        self.controller = controller
        self.desktop = desktop
        self._closed = False
        self._after_id: str | None = None
        self._editor_text = ""
        self._syncing_editor = False
        self._editor_focused = False
        self._error: str | None = None
        self._error_baseline: str | None = None
        self._last_running: bool | None = None
        self._primary_button_action = "start"
        self._primary_button_intent: str | None = None

        root.title("HumanType · Python")
        root.configure(background=self.BACKGROUND)
        available_height = max(400, root.winfo_screenheight() - 80)
        root.geometry(f"560x{min(740, available_height)}")
        root.minsize(500, min(600, available_height))
        root.option_add("*Font", "{Segoe UI} 9")
        root.option_add("*selectBackground", self.WHITE)
        root.option_add("*selectForeground", self.BACKGROUND)
        root.option_add("*TCombobox*Listbox.background", self.SURFACE)
        root.option_add("*TCombobox*Listbox.foreground", self.FOREGROUND)
        root.option_add("*TCombobox*Listbox.selectBackground", self.WHITE)
        root.option_add("*TCombobox*Listbox.selectForeground", self.BACKGROUND)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_styles()
        self._build_layout()
        self._set_editor_text(self.controller.text or "")
        self._render()
        root.after_idle(self._tick)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        # The clam theme can be configured completely, unlike Windows' theme.
        style.theme_use("clam")
        style.configure(
            "HumanType.TCombobox",
            fieldbackground=self.SURFACE,
            background=self.SURFACE,
            foreground=self.FOREGROUND,
            arrowcolor=self.FOREGROUND,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            selectbackground=self.WHITE,
            selectforeground=self.BACKGROUND,
            padding=5,
        )
        style.map(
            "HumanType.TCombobox",
            fieldbackground=[("readonly", self.SURFACE), ("disabled", self.BACKGROUND)],
            foreground=[("disabled", "#686868"), ("readonly", self.FOREGROUND)],
            background=[("active", "#363636"), ("disabled", self.BACKGROUND)],
            arrowcolor=[("disabled", "#686868"), ("readonly", self.FOREGROUND)],
        )

    def _label(
        self,
        parent: tk.Misc,
        text: str = "",
        *,
        color: str | None = None,
        font: tuple[str, int] | tuple[str, int, str] = ("Segoe UI", 9),
        background: str | None = None,
        **kwargs,
    ) -> tk.Label:
        kwargs.setdefault("anchor", "w")
        return tk.Label(
            parent,
            text=text,
            background=background or self.BACKGROUND,
            foreground=color or self.FOREGROUND,
            font=font,
            **kwargs,
        )

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            background=self.WHITE if primary else self.SURFACE,
            foreground=self.BACKGROUND if primary else self.FOREGROUND,
            activebackground="#d0d0d0" if primary else "#363636",
            activeforeground=self.BACKGROUND if primary else self.WHITE,
            disabledforeground="#777777",
            highlightbackground=self.BORDER,
            highlightcolor=self.WHITE,
            highlightthickness=1,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=6,
            takefocus=True,
        )

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, background=self.BACKGROUND, padx=14, pady=12)
        shell.pack(fill="both", expand=True)
        # Reserve the bottom action area before allocating the flexible content.
        # Buttons remain reachable even on a small RDP display or at high DPI.
        actions = tk.Frame(shell, background=self.BACKGROUND)
        actions.pack(side="bottom", fill="x")
        outer = tk.Frame(shell, background=self.BACKGROUND)
        outer.pack(side="top", fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1, minsize=40)

        header = tk.Frame(outer, background=self.BACKGROUND)
        header.grid(row=0, column=0, sticky="ew")
        self._label(header, "HumanType", font=("Segoe UI", 21, "bold")).pack(side="left")
        self._label(header, "PYTHON", color=self.MUTED, font=("Segoe UI", 9, "bold")).pack(side="right")
        self._label(
            outer,
            "Enter or paste text → Start → click your field → press letters.",
            color=self.MUTED,
            pady=5,
        ).grid(row=1, column=0, sticky="ew")

        target = tk.Frame(outer, background=self.SURFACE, padx=10, pady=5)
        target.grid(row=2, column=0, sticky="ew", pady=(2, 7))
        self._label(
            target,
            "TYPING LOCATION",
            color=self.MUTED,
            background=self.SURFACE,
            font=("Segoe UI", 7, "bold"),
        ).pack(fill="x")
        self.target_label = self._label(
            target,
            "Click a text field in another app",
            background=self.SURFACE,
            pady=2,
        )
        self.target_label.pack(fill="x")

        preview_header = tk.Frame(outer, background=self.BACKGROUND)
        preview_header.grid(row=3, column=0, sticky="ew", pady=(0, 5))
        self._label(
            preview_header, "CLIPBOARD TEXT", color=self.MUTED, font=("Segoe UI", 9, "bold")
        ).pack(side="left")
        self.paste_button = self._button(preview_header, "Paste", self.paste, primary=True)
        self.paste_button.pack(side="right")

        preview_frame = tk.Frame(outer, background=self.BORDER, padx=1, pady=1)
        preview_frame.grid(row=4, column=0, sticky="nsew")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview = tk.Text(
            preview_frame,
            wrap="word",
            height=4,
            width=1,
            state="normal",
            background=self.SURFACE,
            foreground=self.FOREGROUND,
            insertbackground=self.WHITE,
            selectbackground=self.WHITE,
            selectforeground=self.BACKGROUND,
            borderwidth=0,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 9),
            padx=8,
            pady=5,
            takefocus=True,
            undo=True,
            autoseparators=True,
        )
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<<Modified>>", self._on_editor_modified)
        self.preview.bind("<FocusIn>", self._on_editor_focus_in)
        self.preview.bind("<FocusOut>", self._on_editor_focus_out)
        self.placeholder = self._label(
            self.preview,
            "paste text here",
            color=self.MUTED,
            background=self.SURFACE,
            font=("Consolas", 9),
            cursor="xterm",
        )
        self.placeholder.bind("<Button-1>", self._focus_editor)
        scrollbar = tk.Scrollbar(
            preview_frame,
            orient="vertical",
            command=self.preview.yview,
            background="#444444",
            activebackground="#777777",
            troughcolor=self.SURFACE,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=scrollbar.set)

        settings = tk.Frame(outer, background=self.BACKGROUND)
        settings.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        settings.columnconfigure(0, weight=1)
        self._label(
            settings,
            "One keypress = one pasted character",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self._label(
            settings,
            "After the countdown, press any letter or Space.\n"
            "Your key is replaced by the next pasted character. Holding a key does not repeat.",
            color=self.MUTED,
            font=("Segoe UI", 8),
            justify="left",
            pady=4,
        ).grid(row=1, column=0, sticky="w")

        options = tk.Frame(outer, background=self.BACKGROUND)
        options.grid(row=6, column=0, sticky="ew")
        options.columnconfigure(2, weight=1)
        self._label(options, "Countdown", color=self.MUTED).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.countdown_var = tk.StringVar(value=str(int(self.controller.countdown_seconds)))
        self.countdown_spin = tk.Spinbox(
            options,
            from_=1,
            to=10,
            textvariable=self.countdown_var,
            width=3,
            background=self.SURFACE,
            foreground=self.FOREGROUND,
            disabledbackground=self.BACKGROUND,
            disabledforeground="#686868",
            buttonbackground="#444444",
            insertbackground=self.WHITE,
            selectbackground=self.WHITE,
            selectforeground=self.BACKGROUND,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.WHITE,
        )
        self.countdown_spin.grid(row=0, column=1, sticky="w", ipadx=3, ipady=3)
        self._label(options, "seconds", color=self.MUTED).grid(row=0, column=2, sticky="w", padx=(7, 10))
        self._label(options, "Line breaks", color=self.MUTED).grid(row=0, column=3, padx=(0, 8))
        selected_mode = next(
            (label for label, mode in self.NEWLINE_MODES.items() if mode == self.controller.newline_mode),
            "Shift+Enter",
        )
        self.newline_var = tk.StringVar(value=selected_mode)
        self.newline_combo = ttk.Combobox(
            options,
            textvariable=self.newline_var,
            values=list(self.NEWLINE_MODES),
            state="readonly",
            style="HumanType.TCombobox",
            width=11,
            takefocus=True,
        )
        self.newline_combo.grid(row=0, column=4, sticky="e")
        self._label(
            outer,
            "Enter mode can submit a single-line field.",
            color=self.MUTED,
            font=("Segoe UI", 8),
            pady=2,
        ).grid(row=7, column=0, sticky="ew")

        progress_header = tk.Frame(outer, background=self.BACKGROUND)
        progress_header.grid(row=8, column=0, sticky="ew", pady=(4, 3))
        self.progress_label = self._label(progress_header, "0 / 0 characters", color=self.MUTED, font=("Segoe UI", 9))
        self.progress_label.pack(side="left")
        self.percent_label = self._label(progress_header, "0%", color=self.MUTED, font=("Segoe UI", 9))
        self.percent_label.pack(side="right")
        self.progress_canvas = tk.Canvas(
            outer,
            height=5,
            background="#373737",
            borderwidth=0,
            highlightthickness=0,
        )
        self.progress_canvas.grid(row=9, column=0, sticky="ew")
        self.progress_canvas.bind("<Configure>", lambda _event: self._paint_progress())

        self.status_label = self._label(
            outer,
            "Click your field during the countdown. Then press letters or Space to advance.",
            justify="left",
            wraplength=500,
            height=2,
            pady=5,
        )
        self.status_label.grid(row=10, column=0, sticky="ew")
        outer.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(200, event.width - 44)))

        buttons = tk.Frame(actions, background=self.BACKGROUND)
        buttons.pack(fill="x", pady=(3, 5))
        for column in range(3):
            buttons.columnconfigure(column, weight=1, uniform="actions")
        self.start_button = self._button(buttons, "Start", self._activate_primary_button, primary=True)
        # Keep the action the user pressed even if focusing this app pauses the
        # controller before Tk invokes the button on mouse/key release.
        self.start_button.bind("<ButtonPress-1>", self._capture_primary_button_intent, add="+")
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.continue_button = self._button(buttons, "Continue", self.resume)
        self.continue_button.grid(row=0, column=1, sticky="ew", padx=6)
        self.clear_button = self._button(buttons, "Clear", self.clear)
        self.clear_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        self._label(
            actions,
            "Escape pauses. Clear discards the remaining text.",
            color=self.MUTED,
            font=("Segoe UI", 8),
            anchor="center",
            pady=2,
        ).pack(fill="x")

    def _apply_settings(self) -> None:
        try:
            countdown = float(self.countdown_var.get())
        except ValueError:
            raise TypingError("Set the countdown to a number from 1 to 10 seconds.") from None
        if not 1 <= countdown <= 10:
            raise TypingError("Set the countdown to a number from 1 to 10 seconds.")
        self.controller.countdown_seconds = countdown
        self.controller.newline_mode = self.NEWLINE_MODES[self.newline_var.get()]

    def _set_editor_text(self, text: str) -> None:
        """Synchronize only after an explicit load/clear, never from the tick loop."""
        self._syncing_editor = True
        try:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", text)
            self.preview.edit_modified(False)
            self._editor_text = text
            if self.controller.state in {"countdown", "typing"}:
                self.preview.configure(state="disabled")
        finally:
            self._syncing_editor = False
        self._update_placeholder()

    def _on_editor_modified(self, _event: tk.Event | None = None) -> None:
        if self._closed or self._syncing_editor or not self.preview.edit_modified():
            return
        self.preview.edit_modified(False)
        self._editor_text = self.preview.get("1.0", "end-1c")
        self._error = None
        self._update_placeholder()
        self._render()

    def _update_placeholder(self) -> None:
        if not self._editor_text and not self._editor_focused:
            self.placeholder.place(x=8, y=5)
        else:
            self.placeholder.place_forget()

    def _on_editor_focus_in(self, _event: tk.Event | None = None) -> None:
        self._editor_focused = True
        self._update_placeholder()

    def _on_editor_focus_out(self, _event: tk.Event | None = None) -> None:
        self._editor_focused = False
        self._update_placeholder()

    def _focus_editor(self, _event: tk.Event | None = None) -> str:
        self.preview.focus_set()
        self._on_editor_focus_in()
        return "break"

    def _refresh_editor_text(self) -> str:
        # Read directly at action time even if Tk has not delivered <<Modified>> yet.
        self._editor_text = self.preview.get("1.0", "end-1c")
        self._update_placeholder()
        return self._editor_text

    def _load_editor_for_start(self) -> None:
        text = self._refresh_editor_text()
        if not text:
            raise TypingError("Enter or paste text in the text box before starting.")
        if text != (self.controller.text or ""):
            self.controller.paste(text)
            # Reflect the controller's whitespace/control-code normalization once.
            self._set_editor_text(self.controller.text)

    def _display_progress(self) -> tuple[int, int]:
        if self._editor_text != (self.controller.text or ""):
            return 0, len(self._editor_text)
        return self.controller.position, self.controller.total

    def _perform(self, action: Callable[[], None]) -> None:
        if self._closed:
            return
        self._error = None
        try:
            action()
        except (TypingError, RuntimeError, OSError, ValueError, tk.TclError) as exc:
            self._show_error(str(exc))
        self._render()

    def _show_error(self, message: str) -> None:
        self._error = message
        self._error_baseline = self.controller.message

    def paste(self) -> None:
        def action() -> None:
            self.controller.paste(self.desktop.read_clipboard_text())
            self._set_editor_text(self.controller.text)

        self._perform(action)

    def clear(self) -> None:
        def action() -> None:
            self.controller.clear()
            self._set_editor_text("")
            self.preview.edit_reset()

        self._perform(action)

    def start(self) -> None:
        def action() -> None:
            if self.controller.state not in {"countdown", "typing"}:
                self._apply_settings()
                self._load_editor_for_start()
            self.controller.start()

        self._perform(action)

    def _capture_primary_button_intent(self, _event: tk.Event) -> None:
        self._primary_button_intent = self._primary_button_action

    def _activate_primary_button(self) -> None:
        action = self._primary_button_intent or self._primary_button_action
        self._primary_button_intent = None
        if action == "pause":
            self.stop()
        else:
            self.start()

    def resume(self) -> None:
        def action() -> None:
            text = self._refresh_editor_text()
            if not text:
                raise TypingError("Enter or paste text in the text box before starting.")
            if text != (self.controller.text or ""):
                raise TypingError("The text changed. Press Start to begin the edited text from the first character.")
            self._apply_settings()
            self.controller.resume()

        self._perform(action)

    def stop(self) -> None:
        self._perform(self.controller.stop)

    def _tick(self) -> None:
        if self._closed:
            return
        try:
            self.controller.tick()
        except Exception as exc:
            # A native backend failure should leave a stopped, usable app, rather
            # than silently terminating the Tk callback and its future ticks.
            try:
                self.controller.stop("Typing stopped because of an input error.")
            except Exception:
                pass
            self._show_error(f"Typing stopped: {exc}")
        self._render()
        self._after_id = self.root.after(15, self._tick)

    def _render(self) -> None:
        state = self.controller.state
        running = state in {"countdown", "typing"}
        changed = self._editor_text != (self.controller.text or "")
        has_text = bool(self._editor_text)
        self.paste_button.configure(state="disabled" if running else "normal")
        self.clear_button.configure(state="normal" if has_text or self.controller.total else "disabled")
        start_label = "Pause" if running else "Resume" if state == "paused" and not changed else "Start"
        self._primary_button_action = "pause" if running else "start"
        self.start_button.configure(
            text=start_label,
            state="normal" if running or (has_text and (changed or state in {"ready", "paused"})) else "disabled",
        )
        self.continue_button.configure(state="normal" if state == "paused" and has_text and not changed else "disabled")
        if running != self._last_running:
            self.countdown_spin.configure(state="disabled" if running else "normal")
            self.newline_combo.configure(state="disabled" if running else "readonly")
            self.preview.configure(state="disabled" if running else "normal")
            self._last_running = running

        name = self.controller.target_name or "Click a text field in another app"
        # Fit long window titles without letting the label enlarge the window.
        self.target_label.configure(text=name if len(name) <= 66 else name[:63] + "…")
        position, total = self._display_progress()
        self.progress_label.configure(text=f"{position:,} / {total:,} characters")
        fraction = position / total if total else 0
        self.percent_label.configure(text=f"{min(100, int(fraction * 100))}%")
        self._paint_progress()
        if self._error and self.controller.message != self._error_baseline:
            self._error = None
        message = self.controller.message
        if changed and not running:
            message = "Edited text is ready. Start begins from the first character." if has_text else "Enter or paste text in the text box."
        self.status_label.configure(text=self._error or message)

    def _paint_progress(self) -> None:
        if self._closed or not hasattr(self, "progress_canvas"):
            return
        position, total = self._display_progress()
        fraction = position / total if total else 0
        width = self.progress_canvas.winfo_width()
        height = self.progress_canvas.winfo_height()
        self.progress_canvas.delete("fill")
        if fraction > 0:
            self.progress_canvas.create_rectangle(
                0, 0, width * min(1.0, fraction), height,
                fill=self.WHITE, outline="", tags="fill",
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        try:
            self.controller.stop()
        finally:
            self.root.destroy()
