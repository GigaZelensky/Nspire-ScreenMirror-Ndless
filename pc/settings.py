"""Per-user desktop preferences; never persist sessions or debug output paths."""

import argparse
import json
import os
from pathlib import Path
import tempfile
from clock_options import valid_clock


def integer(low, high):
    return lambda value: type(value) is int and low <= value <= high


def sensitivity(value):
    return type(value) in (int, float) and 0.1 <= value <= 4


def desktop_size(value):
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and all(integer(120, 4096)(dimension) for dimension in value)
    )


FIELDS = {
    "monitor": integer(0, 16),
    "fps": integer(1, 60),
    "crf": integer(8, 35),
    "mpeg4_q": integer(1, 8),
    "mode": lambda value: value in ("h264", "mpeg4", "lossless"),
    "preset": lambda value: value in ("ultrafast", "veryfast", "fast", "medium"),
    "clock": valid_clock,
    "capture": lambda value: value in (None, "auto", "dxgi", "gdi", "mss", "wayland"),
    "desktop_size": desktop_size,
    "pointer_gain": sensitivity,
    "scroll_gain": sensitivity,
    "keyboard_layout": lambda value: value in ("auto", "us", "fr"),
    "selected_tab": integer(0, 2),
}
for field in (
    "virtual_display",
    "input",
    "auto_quality",
    "confine_pointer",
    "reverse_scroll",
    "test_pattern",
    "diagnostic_log",
):
    FIELDS[field] = lambda value: type(value) is bool


def settings_path():
    base = (
        Path(os.environ["LOCALAPPDATA"])
        if os.name == "nt"
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    )
    return base / "ND Screen Mirror" / "settings.json"


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else settings_path()
        self.values = {}
        self.notice = None
        self.newer_version = False
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("version") != 1:
                self.newer_version = True
                raise ValueError("Unsupported settings version")
            values = document.get("settings")
            if not isinstance(values, dict):
                raise ValueError("Invalid settings document")
            self.values = self.valid(values)
            if len(self.values) != len(values):
                self.notice = "Some saved settings were invalid and were ignored."
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.notice = "Saved settings could not be read; using defaults."

    @staticmethod
    def valid(values):
        result = {
            key: value for key, value in values.items() if key in FIELDS and FIELDS[key](value)
        }
        if "desktop_size" in result:
            result["desktop_size"] = list(result["desktop_size"])
        return result

    def merge(self, defaults, argv=()):
        explicit = {argument.split("=", 1)[0] for argument in argv if argument.startswith("--")}
        values = vars(defaults).copy()
        for key, value in self.values.items():
            flag = "--" + key.replace("_", "-")
            negative = "--no-" + key.replace("_", "-")
            if flag not in explicit and negative not in explicit:
                values[key] = tuple(value) if key == "desktop_size" else value
        return argparse.Namespace(**values)

    def save(self, values):
        if self.newer_version:
            raise OSError("Cannot overwrite settings from an unsupported app version")
        updated = {**self.values, **self.valid(values)}
        if updated == self.values and self.path.is_file():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump({"version": 1, "settings": updated}, file, indent=2, allow_nan=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.values = updated
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
