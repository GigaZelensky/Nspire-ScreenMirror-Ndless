"""Exercise fixed-byte clock comparison without opening a USB device."""

from pathlib import Path
import contextlib
import json
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.benchmarks.replay_usb import replay, load_capture
from wire import ACK, HEADER, CLOCK, CAPS, POLL, CAP_CLOCK, CAP_BULK, OK, RESYNC, CLOSING


class Receiver:
    def __init__(self, stop):
        self.closed = self.paused = False
        self.clock = self.accepted = self.displayed = 0
        self.frames = []
        self.fail = False
        self.closing = False

    @contextlib.contextmanager
    def connection(self):
        yield self

    def response(self, session, status=OK, caps=False):
        return ACK.pack(
            b"NDA1",
            1,
            status,
            2,
            int(status != OK),
            session,
            self.accepted,
            self.displayed,
            254,
            CAP_CLOCK | CAP_BULK if caps else 0,
            self.clock if caps else 0,
        )

    def exchange(self, packet):
        session = HEADER.unpack_from(packet)[5]
        if packet[5] == CLOCK:
            self.clock = HEADER.unpack_from(packet)[6]
        if packet[5] == POLL:
            self.displayed = self.accepted
        return self.response(session, caps=packet[5] == CAPS)

    def batch(self, packets):
        assert all(len(p) <= 254 for p in packets)
        header = HEADER.unpack_from(packets[0])
        self.frames.append(b"".join(p[32:] for p in packets))
        self.accepted = self.displayed = header[6]
        return self.response(header[5], CLOSING if self.closing else RESYNC if self.fail else OK)

    def pause_session(self, session):
        self.paused = True

    def close(self):
        self.closed = True


instances = []


def factory(stop):
    receiver = Receiver(stop)
    instances.append(receiver)
    return receiver


frames = [dict(frame=901, bytes=600, key=True), dict(frame=902, bytes=3, key=False)]
data = bytes(range(200)) * 3 + b"end"
result = replay(data, frames, 492, 60, threading.Event(), factory)
assert not result["failure"] and result["completed"] == 2 and result["duration"] > 0
assert instances[-1].frames == [data[:600], b"end"]
assert instances[-1].closed and instances[-1].paused and instances[-1].clock == 492
result = replay(data, frames, 492, 60, threading.Event(), factory, repeats=2)
assert not result["failure"] and result["completed"] == result["expected"] == 4
assert instances[-1].frames == [data[:600], b"end"] * 2
assert instances[-1].accepted == 904


def failing(stop):
    receiver = factory(stop)
    receiver.fail = True
    return receiver


result = replay(data, frames, 396, 60, threading.Event(), failing)
assert result["failure"] and result["submitted_frame"] == 901
assert instances[-1].closed and not instances[-1].paused


def closing(stop):
    receiver = factory(stop)
    receiver.closing = True
    return receiver


# An immediate simulated close may fit within one Windows clock tick. Check
# that valid zero-duration case explicitly rather than depending on wall time.
with patch("tools.benchmarks.replay_usb.time.monotonic", return_value=100.0):
    result = replay(data, frames, 492, 60, threading.Event(), closing)
assert result["closed_by_receiver"] and result["failure"] is None and result["duration"] == 0
assert instances[-1].closed and not instances[-1].paused

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "capture.h264"
    path.write_bytes(data)
    path.with_suffix(".json").write_text(json.dumps(dict(frames=frames)))
    assert load_capture(path) == (data, frames)
    for invalid in (
        [dict(frame=0, bytes=len(data), key=True)],
        [dict(frame=901, bytes=len(data), key=False)],
        [dict(frame=901, bytes=65537, key=True)],
        [dict(frame=901, bytes=len(data), key=1)],
        [],
    ):
        path.with_suffix(".json").write_text(json.dumps(dict(frames=invalid)))
        try:
            load_capture(path)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
print(
    "PASS captured-byte replay: clock confirmation, negotiated fragments, final pause, failure cleanup and metadata bounds"
)
