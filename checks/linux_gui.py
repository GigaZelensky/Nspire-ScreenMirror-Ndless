"""Hidden Linux GUI controls and Stop/Start state; no capture, USB or real input."""

import argparse
from pathlib import Path
import sys
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from desktop import gui

launches = []
failures = []
original = tk.Tk


class Window(original):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.after(150, self.start)

    def widgets(self, parent=None):
        for child in (parent or self).winfo_children():
            yield child
            yield from self.widgets(child)

    def report_callback_exception(self, kind, error, traceback):
        failures.append(error)
        self.destroy()

    def start(self):
        widgets = list(self.widgets())
        buttons = {w.cget("text"): w for w in widgets if w.winfo_class() == "TCheckbutton"}
        assert not buttons["Control this PC with the calculator"].instate(["disabled"])
        assert buttons["Control this PC with the calculator"].instate(["selected"])
        assert buttons["Own Windows display"].instate(["disabled"])
        assert buttons["Keep pointer on mirrored display"].instate(["disabled"])
        captures = next(
            w for w in widgets if w.winfo_class() == "TCombobox" and "WAYLAND" in w.cget("values")
        )
        assert captures.get() == "AUTO"
        layout = next(
            w
            for w in widgets
            if w.winfo_class() == "TCombobox" and "French (AZERTY)" in w.cget("values")
        )
        layout.set("French (AZERTY)")
        next(
            w
            for w in widgets
            if w.winfo_class() == "TButton" and w.cget("text") == "Start mirroring"
        ).invoke()
        self.after(500, self.verify)

    def verify(self):
        assert launches and launches[0].input and launches[0].capture == "auto"
        assert launches[0].keyboard_layout == "fr"
        buttons = {w.cget("text"): w for w in self.widgets() if w.winfo_class() == "TCheckbutton"}
        assert not buttons["Control this PC with the calculator"].instate(["disabled"])
        assert not buttons["Reverse scrolling"].instate(["disabled"])
        assert buttons["Own Windows display"].instate(["disabled"])
        assert buttons["Keep pointer on mirrored display"].instate(["disabled"])
        self.tk.call(self.protocol("WM_DELETE_WINDOW"))


def stream(args, stop, report, **unused):
    launches.append(args)
    report("Stopped")


defaults = argparse.Namespace(monitor=1, fps=30, crf=14, test_pattern=False)
with patch.object(tk, "Tk", Window):
    gui(defaults, stream, lambda args: None)
assert not failures, failures
print(
    "PASS Linux GUI input/sensitivity enabled, Auto/Wayland selection and unavailable Windows controls stay disabled"
)
