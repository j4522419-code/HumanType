# HumanType Python

A black-and-white Windows app that replaces each physical letter or Space press with the next character from your pasted text. Type or paste directly into the **CLIPBOARD TEXT** box showing **paste text here**, or use the **Paste** button. Start/Pause, Continue, and Clear are GUI buttons. The app reads your Windows clipboard only when you use Paste or the editor's normal paste shortcut.

For example, if you paste `Hello` and press `a`, `s`, `d`, `f`, `g`, the destination receives `Hello`. Your actual pressed letters are hidden. Each fresh letter or Space press advances by one character, including spaces, punctuation, emoji, and line breaks in the pasted text. Holding a key down does not repeat or advance further. Nothing is typed without a new keypress.

Other physical numbers, punctuation, and editing/navigation keys are ignored while the job is active and do not advance the pasted text. The app's generated keystrokes still pass through. Normal input returns on Pause, Clear, completion, switching fields/windows, or exiting the app.

## Use the executable

Close any earlier copy and open **HumanTypePython-Editable.exe** from the release download (or **dist/HumanTypePython-Editable.exe** after a local build). Python and .NET do not need to be installed to run this bundled version. Windows 10/11, 64-bit.

1. Click **paste text here** in the **CLIPBOARD TEXT** box and type or paste your text directly with **Ctrl+V**. You can also click **Paste** to replace the box with your clipboard text.
2. Set the countdown. Three seconds is the default; increase it if switching windows takes longer.
3. Click **Start**, then click directly inside the destination text field during the countdown. Put the cursor exactly where typing should begin. This manual field selection avoids depending on Windows allowing focus transfer.
4. After the countdown, press any letter or Space. Each separate press inserts exactly the next pasted character, regardless of which letter you pressed.
5. **Pause** keeps your place. **Resume** or **Continue** re-enables keypress replacement after another countdown. **Clear** immediately halts the job and discards the queued text. It does not delete anything already typed into the destination or erase your Windows clipboard.

You can edit the text while paused. Starting edited text begins a fresh queue; resuming unchanged text keeps the previous position. The hint is only a visual placeholder and is never included in the queued text. The editor is locked during the countdown and active typing.

Clicking away from the destination automatically pauses typing. The app returns to the saved window when you resume; if Windows refuses to focus it, click its text field during the countdown. Keep the cursor at the intended insertion point. Browser fields often share a native control, so the app cannot restore an exact caret position after you move it yourself.

**Escape** pauses and immediately restores normal input. Ctrl, Alt, Windows, and function keys also release the filter and pause typing so system shortcuts remain usable. The mouse is never blocked. Shift remains usable; character insertion waits for it to be released. Fn+Alt was not substituted with a different start shortcut because Fn reporting depends on keyboard hardware.

The filter applies only to physical key events while the saved field has focus. Injected input from accessibility software or remote-input tools is allowed. If Windows cannot enable the filter, the job pauses with an error instead of typing with unfiltered keys. A short heartbeat releases the filter if the app stops responding. Keyboard event contents are not logged or saved.

## Text and compatibility

- Typing uses layout-checked physical key events for ordinary ASCII characters, with Unicode packets for other text. A failed or partially accepted Windows input call pauses with an error instead of silently advancing the queue.
- **Shift+Enter** is the default line break, useful in many browser editors. **Enter** works in ordinary editors but may submit a single-line field or a message. **Text only** sends a literal carriage-return text packet for apps that support it. Choose the mode that matches your destination.
- Tabs are sent as literal text packets, not Tab navigation. Some fields ignore tabs. Line endings are normalized; other text, including Unicode and emoji, is retained. Non-text control codes are removed.
- Your keypresses control the speed. There is no automatic timer-driven typing.
- Windows prevents ordinary apps from typing into elevated or protected apps. Run both apps normally. Some games, secure fields, remote sessions, and specialized editors reject simulated input. A successful Windows call confirms that events entered the input queue; it cannot prove that every target application accepted the text.

No application can guarantee compatibility with every Windows text field. Automated tests cover the controller, editor state, keyboard filtering, and native API boundary. The executable includes a headless self-test; verify live input behavior in your intended editor before relying on it.

## Run the Python source

Extract the source folder, install Python 3.12+ for Windows with Tkinter, and run:

```powershell
python main.py
```

Runtime code uses Python's standard library only. To build the single-file executable:

```powershell
python -m pip install -r requirements-build.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

Run the test suite without opening a window or sending input:

```powershell
python -m unittest discover -s tests -v
python main.py --self-test --report artifacts\source-self-test.json
.\dist\HumanTypePython-Editable.exe --self-test --report artifacts\exe-self-test.json
```

The self-test uses fake native APIs for input/clipboard/filter tests and performs read-only packet-construction and runtime checks. It never types into another app, installs a live keyboard hook, or reads your clipboard. The build script runs it against the actual bundled executable.

## Privacy

Clipboard text and progress stay in memory until Clear, replacement, or exit. HumanType does not save clipboard text to disk, upload it, or make network requests. Self-test reports contain only test results and diagnostic metadata.

## Technical references

- [Microsoft SendInput API and privilege restrictions](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
- [Keyboard-layout character mapping](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-vkkeyscanexw)
- [Windows low-level keyboard filtering](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)
- [Fn support depends on the OEM](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/system-button-narration)
- [PyInstaller packaging](https://pyinstaller.org/en/stable/usage.html)
