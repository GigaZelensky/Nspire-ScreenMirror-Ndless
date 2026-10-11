"""Set Windows DPI awareness before Tk creates a window."""

import ctypes as c
import os


def initialize():
    if os.name != "nt":
        return
    user = c.WinDLL("user32", use_last_error=True)
    current = user.GetThreadDpiAwarenessContext
    current.argtypes = []
    current.restype = c.c_void_p
    awareness = user.GetAwarenessFromDpiAwarenessContext
    awareness.argtypes = [c.c_void_p]
    awareness.restype = c.c_int
    if awareness(current()) == 2:
        return  # The frozen executable's manifest already selected per-monitor DPI.
    configure = user.SetProcessDpiAwarenessContext
    configure.argtypes = [c.c_void_p]
    configure.restype = c.c_int
    if not configure(c.c_void_p(-4)):
        # Windows fixes process awareness before the first HWND is created.
        # Report an unexpected failure rather than silently accepting blurry UI.
        raise c.WinError(c.get_last_error())
