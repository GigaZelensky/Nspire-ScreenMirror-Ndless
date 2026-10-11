"""Preference round trips, safe writes, CLI overrides and real GUI reopening."""

import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from settings import Settings
import mirror
from clock_options import parse_clock

with patch("sys.argv", ["mirror.py"]), patch("mirror.gui") as launch:
    mirror.main()
    assert launch.call_args.args[0].preset == "medium"
    assert launch.call_args.args[0].desktop_size == (480, 360)
    assert launch.call_args.kwargs["remember"] is True
    assert launch.call_args.args[0].connection_log is None
    assert launch.call_args.args[0].keyboard_layout == "auto"

with patch("sys.argv", ["mirror.py", "--keyboard-layout", "fr"]), patch("mirror.gui") as launch:
    mirror.main()
    assert launch.call_args.args[0].keyboard_layout == "fr"

with patch("sys.argv", ["mirror.py", "--diagnostic-log"]), patch("mirror.gui") as launch:
    mirror.main()
    assert launch.call_args.args[0].diagnostic_log is True
    assert launch.call_args.args[0].connection_log is not None

with patch("sys.argv", ["mirror.py", "--clock", "480"]), patch("mirror.gui") as launch:
    mirror.main()
    assert launch.call_args.args[0].clock == 480
for text in ("480", "480 MHz", " 480 mhz "):
    assert parse_clock(text) == 480
for text in ("", "48", "400", "500", "480.5", "288", "NaN", "-480"):
    try:
        parse_clock(text)
        raise AssertionError(f"Invalid clock accepted: {text}")
    except ValueError:
        pass

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "settings.json"
    store = Settings(path)
    values = dict(
        preset="medium",
        keyboard_layout="fr",
        desktop_size=(480, 360),
        fps=30,
        clock=480,
        diagnostic_log=False,
        auto_quality=False,
        reverse_scroll=True,
        scroll_gain=0.5,
        pointer_gain=1.5,
        selected_tab=1,
        crf=12,
        mpeg4_q=2,
    )
    store.save({**values, "metrics": "private.jsonl", "start": True})
    reread = Settings(path)
    assert reread.values == {**values, "desktop_size": [480, 360]}
    defaults = argparse.Namespace(
        preset="fast", fps=25, auto_quality=True, input=True, keyboard_layout="us"
    )
    merged = reread.merge(defaults, ["--preset=fast", "--auto-quality", "--fps", "25"])
    assert merged.preset == defaults.preset and merged.fps == defaults.fps
    assert merged.auto_quality is True and merged.desktop_size == (480, 360)
    assert merged.keyboard_layout == "fr"
    assert reread.merge(defaults, ["--keyboard-layout=us"]).keyboard_layout == "us"
    before = path.read_bytes()
    with patch("settings.os.replace", side_effect=OSError("test write failure")):
        try:
            reread.save({"fps": 25})
            raise AssertionError("Failed replacement appeared successful")
        except OSError:
            pass
    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))
    store.save(
        {
            "scroll_gain": float("nan"),
            "pointer_gain": 10**1000,
            "fps": True,
            "desktop_size": [0, 360],
        }
    )
    assert store.values == reread.values
    path.write_text("{broken", encoding="utf-8")
    assert Settings(path).notice
    path.write_text(json.dumps({"version": 99, "settings": {}}), encoding="utf-8")
    future = Settings(path)
    try:
        future.save(values)
        raise AssertionError("Future settings were overwritten")
    except OSError:
        pass

    if sys.platform != "win32":
        print("PASS preference round trips, explicit overrides, invalid values and atomic writes")
        sys.exit(0)

    # The real GUI must save edits even when the user closes without Start.
    import tkinter as tk

    path.unlink()
    original_tk = tk.Tk
    phase = [0]

    class Window(original_tk):
        def __init__(self):
            super().__init__()
            self.withdraw()
            self.after(150, self.edit_or_verify)

        def widgets(self, parent=None):
            for child in (parent or self).winfo_children():
                yield child
                yield from self.widgets(child)

        def edit_or_verify(self):
            logs = next(
                widget
                for widget in self.widgets()
                if widget.winfo_class() == "TCheckbutton"
                and widget.cget("text") == "Save diagnostic logs"
            )
            clock = next(
                widget
                for widget in self.widgets()
                if widget.winfo_class() == "TCombobox" and "480 MHz" in widget.cget("values")
            )
            assert str(clock.cget("state")) == "normal"
            assert all(label in clock.cget("values") for label in ("408 MHz", "468 MHz", "132 MHz"))
            preset = next(
                widget
                for widget in self.widgets()
                if widget.winfo_class() == "TCombobox" and "medium" in widget.cget("values")
            )
            reverse = next(
                widget
                for widget in self.widgets()
                if widget.winfo_class() == "TCheckbutton"
                and widget.cget("text") == "Reverse scrolling"
            )
            notebook = next(
                widget for widget in self.widgets() if widget.winfo_class() == "TNotebook"
            )
            controls = self.nametowidget(notebook.tabs()[1])
            scroll = next(
                widget
                for widget in controls.winfo_children()
                if widget.winfo_class() == "TSpinbox" and int(widget.grid_info()["row"]) == 6
            )
            desktop = next(
                widget
                for widget in self.widgets()
                if widget.winfo_class() == "TCombobox" and "Custom" in widget.cget("values")
            )
            if phase[0] == 0:
                assert not logs.instate(["selected"])
                logs.invoke()
                clock.set("480")
                preset.set("veryfast")
                reverse.invoke()
                self.setvar(str(scroll.cget("textvariable")), 0.5)
                desktop.set("Custom")
                desktop.event_generate("<<ComboboxSelected>>")
                entries = [widget for widget in self.widgets() if widget.winfo_class() == "TEntry"]
                assert len(entries) == 2
                self.setvar(str(entries[0].cget("textvariable")), 720)
                self.setvar(str(entries[1].cget("textvariable")), 405)
                notebook.select(1)
            else:
                assert logs.instate(["selected"])
                assert clock.get() == "480 MHz"
                if phase[0] == 1:
                    # A partial edit must preserve the previously saved clock.
                    clock.set("48")
                assert preset.get() == "veryfast" and reverse.instate(["selected"])
                assert float(scroll.get()) == 0.5 and desktop.get() == "Custom"
                assert notebook.index(notebook.select()) == 1
            self.tk.call(self.protocol("WM_DELETE_WINDOW"))

    tk.Tk = Window
    defaults = argparse.Namespace(
        monitor=1,
        fps=30,
        crf=14,
        test_pattern=False,
        virtual_display=False,
        desktop_size=(480, 360),
        preset="medium",
    )
    try:
        mirror.gui(defaults, remember=True, settings_path=path)
        assert Settings(path).values["preset"] == "veryfast"
        assert Settings(path).values["desktop_size"] == [720, 405]
        assert Settings(path).values["clock"] == 480
        phase[0] = 1
        mirror.gui(defaults, remember=True, settings_path=path)
        assert Settings(path).values["clock"] == 480
        phase[0] = 2
        mirror.gui(defaults, remember=True, settings_path=path)
    finally:
        tk.Tk = original_tk
print(
    "PASS saved settings/GUI reopening, explicit overrides, invalid values and atomic write failure"
)
