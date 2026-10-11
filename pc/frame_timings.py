"""Opt-in buffered host timings; no pixels, keystrokes or screen content."""

import json
import time


class FrameTimings:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", buffering=65536)
        self.last_flush = time.monotonic()

    def write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":")) + "\n")
        now = time.monotonic()
        if now - self.last_flush >= 1:
            self.file.flush()
            self.last_flush = now

    def close(self):
        self.file.close()
