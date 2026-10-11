"""Actual SendInput into this test's own entry only. No other application is targeted."""

import sys
import ctypes as c
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from remote_input import WindowsInput

window = tk.Tk()
window.title("ND Mirror input self-test")
entry = tk.Entry(window)
entry.pack(padx=20, pady=20)
window.update()
entry.focus_force()
window.update()
backend = WindowsInput()


def test():
    assert window.focus_get() is entry, "Own input field is not focused"
    backend.key(65, True)
    backend.key(65, False)
    backend.key(0x10, True)
    backend.key(66, True)
    backend.key(66, False)
    backend.key(0x10, False)
    window.after(100, verify)


def verify():
    assert entry.get() == "aB", entry.get()
    assert c.sizeof(backend.Input) == 40
    print(
        "PASS real Windows SendInput structure, letter/Shift chord, key release into isolated test entry"
    )
    window.destroy()


from ctypes import wintypes as w

user = c.WinDLL("user32")
user.GetForegroundWindow.restype = w.HWND
user.GetAncestor.argtypes = [w.HWND, w.UINT]
user.GetAncestor.restype = w.HWND
user.SetForegroundWindow.argtypes = [w.HWND]
user.SetForegroundWindow.restype = w.BOOL
own = user.GetAncestor(window.winfo_id(), 2)
user.SetForegroundWindow(own)
original_test = test


def test():
    if user.GetForegroundWindow() != own:
        print("SKIP actual injection: test window could not acquire foreground")
        window.destroy()
        return
    try:
        original_test()
    except BaseException:
        window.destroy()
        raise


window.after(250, test)
window.after(3000, window.destroy)
window.mainloop()
