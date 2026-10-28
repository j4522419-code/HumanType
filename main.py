"""HumanType Python entry point: python main.py, or the bundled executable."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HumanType clipboard typing")
    parser.add_argument("--self-test", action="store_true", help="Run tests without opening windows or sending input")
    parser.add_argument("--report", type=Path, help="Write the self-test result as JSON")
    args = parser.parse_args(argv)
    if args.self_test:
        from selftest import run_self_test
        result = run_self_test()
        destination = args.report or Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "self-test-report.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
        if sys.stdout:
            print(json.dumps({"passed": result["passed"], "tests": result["tests"], "report": str(destination)}, ensure_ascii=True))
        return 0 if result["passed"] else 1
    if os.name != "nt":
        parser.error("HumanType requires Windows 10 or Windows 11.")

    import tkinter as tk
    from app import HumanTypeApp
    from core import TypingController
    from win32_backend import WindowsDesktop
    from keyboard_guard import KeyboardGuard

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()

    desktop = WindowsDesktop()
    guard = KeyboardGuard()
    controller = TypingController(desktop, keyboard_guard=guard)
    try:
        root = tk.Tk()
        icon = resource_path("assets/HumanType.ico")
        if icon.exists():
            root.iconbitmap(str(icon))
        application = HumanTypeApp(root, controller, desktop)

        def callback_error(exception_type, exception, traceback):
            controller.stop("Paused because of an application error.")
            application._show_error(f"Paused: {exception}")
            application._render()

        root.report_callback_exception = callback_error
        root.mainloop()
    finally:
        controller.stop()
        guard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
