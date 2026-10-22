"""Exercise automatic table-mode transitions through the real sender loop."""

import argparse
import contextlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
import mirror
from wire import (
    ACK,
    HEADER,
    CAPS,
    DATA,
    HELLO,
    POLL,
    STATS,
    LAST,
    CAP_BULK,
    CAP_STREAM,
    CAP_STATS,
    CAP_SRAM_TABLES,
)


class Receiver:
    def __init__(self, stop, *unused):
        self.stop = stop
        self.accepted = self.displayed = self.ram = self.sram = 0
        self.enabled = True
        self.modes = []
        self.closed = False

    @contextlib.contextmanager
    def connection(self):
        yield self

    def exchange(self, data):
        _, _, kind, flags, _, session, frame, *_ = HEADER.unpack_from(data)
        caps = 0
        if kind == CAPS:
            caps = CAP_BULK | CAP_STREAM | CAP_STATS | CAP_SRAM_TABLES
        if kind == HELLO:
            self.accepted = self.displayed = 0
        elif kind == DATA and flags & LAST:
            self.accepted = frame
            if self.enabled:
                self.sram += 1
            else:
                self.ram += 1
        elif kind == POLL:
            self.displayed = self.accepted
        reply = ACK.pack(b"NDA1", 1, 0, 2, 0, session, self.accepted, self.displayed, 1440, caps, 0)
        if kind == STATS:
            if frame in (3, 4):
                assert self.displayed == self.accepted, (
                    "Mode changed before the previous frame finished"
                )
                self.enabled = frame == 4
                self.modes.append(self.enabled)
            words = [0] * 80
            words[0:2] = [3, 2100]
            words[48:58] = [
                1,
                int(self.enabled),
                len(self.modes),
                self.sram,
                self.ram,
                0,
                0,
                0,
                0,
                2100,
            ]
            words[78] = 5504
            reply += b"NDS1" + struct.pack("<I80I", 80, *words)
        return reply

    def batch(self, packets):
        for data in packets:
            reply = self.exchange(data)
        return reply

    def close(self):
        self.closed = True

    def pause(self, *unused):
        pass


receivers = []


def factory(*args):
    receiver = Receiver(*args)
    receivers.append(receiver)
    return receiver


now = 0


def monotonic():
    global now
    now += 0.25
    return now


original_clock, original_apps = mirror.time.perf_counter, mirror.linking_apps
mirror.time.perf_counter = monotonic
mirror.linking_apps = lambda: []
try:
    with tempfile.TemporaryDirectory() as directory:
        metrics = Path(directory) / "timings.jsonl"
        args = argparse.Namespace(
            test_pattern=True,
            fps=30,
            crf=14,
            backend=None,
            library=None,
            packet_size=None,
            region=None,
            monitor=1,
            persistent=True,
            profile=True,
            sram_ab_seconds=5,
            sram_ab_phases=4,
            frame_metrics=metrics,
        )
        stop = threading.Event()
        messages = []
        mirror.stream(args, stop, messages.append, bridge_factory=factory)
        assert len(receivers) == 1 and receivers[0].closed
        assert receivers[0].modes == [False, True, False, True], receivers[0].modes
        assert receivers[0].ram > 0 and receivers[0].sram > 0
        assert stop.is_set() and any("comparison complete" in line for line in messages)
        rows = [json.loads(line) for line in metrics.read_text().splitlines()]
        assert len([row for row in rows if row["kind"] == "sram_phase"]) == 4
        assert rows[-1]["kind"] == "receiver_profile"
finally:
    mirror.time.perf_counter, mirror.linking_apps = original_clock, original_apps
print(
    "PASS real sender RAM/SRAM phase ordering, frame-boundary drain, timing capture and finite completion"
)
