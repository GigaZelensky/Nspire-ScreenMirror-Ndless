"""Quantizer control: sustained overload, idle noise, mild dips and quality recovery."""

from pathlib import Path
import sys
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from adaptive import AdaptiveMpeg4Quality
from encoder import Encoder

black = Image.new("RGB", (320, 240))


def simulate(c, frames, interval, service, capture=0.003, size=3000, start=0, count=0):
    changes = []
    now = start
    for n in range(1, frames + 1):
        now += interval(n) if callable(interval) else interval
        c.prepare(black, now)
        value = c.observe(now, count + n, service, capture, size)
        if value is not None:
            changes.append((now, value))
        assert 1 <= c.quantizer <= 8
    return now, count + frames, changes


for fps in (30, 40, 50, 60):
    c = AdaptiveMpeg4Quality(fps)
    _, _, changes = simulate(c, 250, 1 / fps, 0.010, size=80)
    assert c.quantizer == 1 and not changes
    c = AdaptiveMpeg4Quality(fps)
    _, _, changes = simulate(
        c, 250, lambda n: 1 / (fps * 0.5 if n % 80 < 40 else fps * 0.85), 0.011, size=80
    )
    assert c.quantizer == 1 and not changes, "Idle scheduler noise reduced detail"
c = AdaptiveMpeg4Quality(50)
now, count, changes = simulate(c, 120, 0.04, 0.04)
assert changes and changes[0][0] < 1.2 and c.quantizer > 1
now, count, recover = simulate(c, 1800, 0.02, 0.008, start=now, count=count)
assert recover and c.quantizer == 1
c = AdaptiveMpeg4Quality(50)
_, _, changes = simulate(c, 1200, 0.05, 0.05, capture=0.04)
assert not changes and c.reason == "desktop capture limited"
c = AdaptiveMpeg4Quality(50)
_, _, changes = simulate(c, 40, 1 / 47, 0.018)
assert not changes, "Brief mild dip reduced quality"
c = AdaptiveMpeg4Quality(50)
now, count, _ = simulate(c, 120, 0.04, 0.04)
assert c.quantizer > 1
now, count, _ = simulate(c, 120, 0.04, 0.010, size=32, start=now, count=count)
assert c.quantizer == 1, "Idle timing noise prevented restoration"

# A failed quality level isn't retried repeatedly under the same load.
c = AdaptiveMpeg4Quality(50)
assert c._apply(500000, 1) == 2
c.feedback.samples.append((1, 1, 0.012, 0.003, 3000, 0, 0, 3000))
assert c._apply(1000000, 3) is None and c.quantizer == 2
assert c._apply(1000000, 12) == 1
# Learned motion feedback acts before the next expensive frame is encoded.
c = AdaptiveMpeg4Quality(50)
c.feedback.safe_rates[3] = 250000
c.prepare(black, 0)
c.prepare(Image.new("RGB", (320, 240), "white"), 0.02)
assert c.prepare(black, 0.04) == 4 and c.predictions == 1
e = Encoder(50, mode="mpeg4", quantizer=5)
identity = id(e.codec)
for q in (1, 8, 3, 1):
    e.set_quality(q)
    assert id(e.codec) == identity and e.quantizer == q and e.codec.qmin == e.codec.qmax == q
for q in (0, 9, 1.5, True):
    try:
        e.set_quantizer(q)
        raise AssertionError("Invalid quantizer accepted")
    except ValueError:
        pass
print(
    "PASS MPEG4 Q1 headroom, bounded live-Q feedback, static sway, capture limits, mild dips and recovery"
)

# Run the real sender with a fake receiver; wall-clock rates/quality telemetry
# must be sent without relying on a calculator-side tick clock.
import argparse
import json
import tempfile
import threading
from host import SlowReceiver
from mirror import stream
from wire import HEADER, ACK, CAPS, DISPLAY_RATE, CAP_DISPLAY_RATE, CAP_MPEG4, OK

receivers = []


class Receiver(SlowReceiver):
    def __init__(self, stop, *unused):
        super().__init__(threading.Event())
        self.outer_stop = stop
        self.rates = []
        receivers.append(self)

    def exchange(self, data):
        _, _, kind, _, _, session, value, *_ = HEADER.unpack_from(data)
        if kind in (CAPS, DISPLAY_RATE):
            if kind == DISPLAY_RATE:
                assert 0 <= value <= 1000
                self.rates.append(value)
            return ACK.pack(
                b"NDA1",
                1,
                OK,
                self.credit,
                self.need_key,
                session,
                self.accepted,
                self.displayed,
                1440,
                CAP_DISPLAY_RATE | CAP_MPEG4 if kind == CAPS else self.accepted,
                0,
            )
        reply = super().exchange(data)
        if self.accepted >= 270:
            self.outer_stop.set()
        return reply


with tempfile.TemporaryDirectory() as directory:
    log = Path(directory) / "connection.log"
    details = Path(directory) / "frames.jsonl"
    args = argparse.Namespace(
        test_pattern=True,
        fps=50,
        crf=14,
        backend=None,
        library=None,
        packet_size=None,
        region=None,
        monitor=1,
        mode="mpeg4",
        input=False,
        auto_quality=True,
        preset="medium",
        connection_log=log,
        frame_metrics=details,
    )
    stats, messages = [], []
    stream(
        args, threading.Event(), messages.append, bridge_factory=Receiver, stats_report=stats.append
    )
    assert len(receivers) == 1 and receivers[0].closed
    reports = [message for message in messages if "FPS displayed" in message]
    assert len(reports) == len(receivers[0].rates) >= 5
    for text, value in zip(reports, receivers[0].rates):
        assert abs(float(text.split()[0]) * 10 - value) <= 1
    assert stats and all(s["auto_quality"] and 1 <= s["mpeg4_q"] <= 8 for s in stats)
    assert all(s["adaptive_bitrate"] is None for s in stats)
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    performance = next(entry for entry in entries if entry["kind"] == "performance")
    assert performance["target_fps"] == 50 and performance["mpeg4_q"] in range(1, 9)
    assert all(
        performance[name] >= 0 for name in ("capture_ms", "encode_ms", "usb_ms", "pacing_wait_ms")
    )
    frames = [json.loads(line) for line in details.read_text().splitlines()]
    assert frames and all(f["bitrate"] is None and 1 <= f["mpeg4_q"] <= 8 for f in frames)
    assert not any("Retrying" in message for message in messages), messages
print(
    "PASS production MPEG4 auto sender, matching host/overlay FPS, bounded timing summaries and active-Q reporting"
)
