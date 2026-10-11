"""Small persistent connection log; frame detail stays in a bounded memory ring."""

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import traceback
import re


def log_path():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "ND Screen Mirror/logs/connection.log"


class ConnectionLog:
    def __init__(
        self, path=None, maximum=512 * 1024, capture_stream=False, stream_limit=16 * 1024 * 1024
    ):
        self.path = Path(path) if path else None
        self.maximum = maximum
        self.frames = deque(maxlen=16)
        self.notice = None
        self.capture_stream = capture_stream
        self.stream_limit = stream_limit
        self.video = []
        self.video_bytes = 0

    def record(self, kind, **fields):
        if not self.path:
            return
        entry = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size >= self.maximum:
                for index in range(2, 0, -1):
                    old = self.path.with_name(self.path.name + (f".{index}" if index else ""))
                    if old.exists():
                        old.replace(self.path.with_name(self.path.name + f".{index + 1}"))
                self.path.replace(self.path.with_name(self.path.name + ".1"))
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            self.notice = f"Could not save connection log: {error}"
            self.path = None

    def status(self, text):
        if "FPS displayed" not in text:
            self.record("status", message=text)

    def new_session(self):
        self.frames.clear()
        self.video.clear()
        self.video_bytes = 0

    @staticmethod
    def receiver(state):
        return {
            name: state[name]
            for name in (
                "status",
                "credit",
                "need_key",
                "accepted",
                "displayed",
                "received",
                "rejected",
                "limit",
            )
            if name in state
        }

    def frame(self, sequence, size, key, state, data=None):
        self.frames.append(
            {"frame": sequence, "bytes": size, "key": key, "receiver": self.receiver(state)}
        )
        self.encoded(sequence, key, data)

    def encoded(self, sequence, key, data):
        """Keep submitted data even if its transfer never receives an ACK."""
        if not self.capture_stream or data is None:
            return
        if self.video and self.video[-1][0] == sequence:
            return
        if key:
            self.video = []
            self.video_bytes = 0
        if key or self.video:
            self.video.append((sequence, key, bytes(data)))
            self.video_bytes += len(data)
            if self.video_bytes > self.stream_limit or len(self.video) > 1800:
                self.video = []
                self.video_bytes = 0

    def save_stream(self, session):
        if not self.path or not self.video:
            return None
        folder = self.path.parent / "failure-streams"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = folder / f"failure-{stamp}-{session:x}.h264"
        output.write_bytes(b"".join(data for _, _, data in self.video))
        output.with_suffix(".json").write_text(
            json.dumps(
                {
                    "session": session,
                    "frames": [
                        {"frame": sequence, "key": key, "bytes": len(data)}
                        for sequence, key, data in self.video
                    ],
                }
            ),
            encoding="utf-8",
        )
        # Keep only our three most recent automatically generated captures.
        pattern = re.compile(r"failure-\d{8}T\d{6}\.\d{6}Z-[0-9a-f]+\.h264")
        captures = sorted(path for path in folder.iterdir() if pattern.fullmatch(path.name))
        for old in captures[:-3]:
            old.unlink()
            old.with_suffix(".json").unlink(missing_ok=True)
        return str(output)

    def failure(self, error, session, state):
        capture = None
        try:
            # A teardown error has no active session. Do not create another
            # copy of the preceding failure and rotate useful captures away.
            capture = self.save_stream(session) if session else None
        except OSError as write_error:
            self.notice = f"Could not save failure video: {write_error}"
        self.record(
            "failure",
            error=str(error),
            error_type=type(error).__name__,
            session=session,
            receiver=self.receiver(state),
            recent_frames=list(self.frames),
            traceback="".join(traceback.format_exception(error)),
            capture=capture,
        )
