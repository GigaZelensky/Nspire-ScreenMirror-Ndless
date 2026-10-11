import argparse
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
import mirror

OriginalTk = tk.Tk


class HiddenWindow(OriginalTk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.after(250, self.verify)

    def verify(self):
        self.update_idletasks()
        scale = self.winfo_fpixels("1i") / 96
        assert self.winfo_reqwidth() / scale < 600 and self.winfo_reqheight() / scale < 520
        print(
            "PASS native PC GUI creation and compact layout:",
            self.winfo_reqwidth(),
            self.winfo_reqheight(),
        )
        for pending in self.tk.call("after", "info"):
            self.after_cancel(pending)
        self.destroy()


tk.Tk = HiddenWindow
for virtual, size in [(False, (320, 240)), (True, (640, 480)), (True, (720, 405))]:
    mirror.gui(
        argparse.Namespace(
            monitor=1,
            fps=30,
            crf=14,
            test_pattern=False,
            virtual_display=virtual,
            desktop_size=size,
        )
    )
