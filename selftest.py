"""Headless diagnostics included in the packaged executable."""
from __future__ import annotations

import ctypes
import io
import os
import platform
import unittest


def run_self_test() -> dict:
    # All tests use fake desktop APIs. None calls the real SendInput/clipboard.
    from tests import test_core, test_win32_backend, test_keyboard_guard, test_keyboard_integration, test_text_editor
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromModule(module) for module in (
        test_core, test_win32_backend, test_keyboard_guard, test_keyboard_integration, test_text_editor))
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    checks = {}
    try:
        import tkinter
        from app import HumanTypeApp
        from win32_backend import INPUT, KEYBDINPUT, MOUSEINPUT, GUITHREADINFO, WinAPI, WindowsDesktop
        from core import Target
        checks["tkinter_import"] = bool(HumanTypeApp and tkinter.TkVersion)
        # Tcl(), unlike Tk(), creates no window or desktop interaction.
        interpreter = tkinter.Tcl()
        checks["tcl_runtime"] = str(interpreter.eval("info patchlevel"))
        sizes = {cls.__name__: ctypes.sizeof(cls) for cls in (INPUT, KEYBDINPUT, MOUSEINPUT, GUITHREADINFO)}
        checks["native_structure_bytes"] = sizes
        expected = {"INPUT": 40, "KEYBDINPUT": 24, "MOUSEINPUT": 32, "GUITHREADINFO": 72} if ctypes.sizeof(ctypes.c_void_p) == 8 else {"INPUT": 28, "KEYBDINPUT": 16, "MOUSEINPUT": 24, "GUITHREADINFO": 48}
        assert sizes == expected, (sizes, expected)
        if os.name == "nt":
            from keyboard_guard import NativeAPI, KBDLLHOOKSTRUCT
            NativeAPI()  # Resolve native functions only; do not install any hook.
            assert ctypes.sizeof(KBDLLHOOKSTRUCT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)
            checks["keyboard_filter_api_binding"] = True
            backend = WindowsDesktop(WinAPI())
            probe_target = Target(0, 0, 1, 0, "Packet construction probe")
            # These APIs only translate characters. SendInput is never called.
            packets = {char: len(backend.build_inputs(char, probe_target, "shift_enter"))
                       for char in ("a", "A", "!", "é", "🙂", "\n", "\t")}
            assert all(value >= 2 for value in packets.values())
            checks["windows_api_binding"] = True
            checks["packet_construction"] = packets
    except Exception as error:
        checks["diagnostic_error"] = str(error)
    return {
        "passed": result.wasSuccessful() and "diagnostic_error" not in checks,
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "desktop_input_sent": False,
        "live_keyboard_hook_installed": False,
        "clipboard_read": False,
        "checks": checks,
        "log": log.getvalue(),
    }
