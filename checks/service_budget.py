"""Capacity fit, controller hysteresis and deadline waits use measured work."""

from pathlib import Path
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from service_budget import ServiceBudget
from adaptive import AdaptiveQuality
import pacing
from PIL import Image

model = ServiceBudget()
for n in range(100):
    size = 1000 + (n % 17) * 150
    model.observe(n / 30, size, 0.010 + 0.005 + size / 200000, 0.007, 0.003, 800000, 0)
capacity = model.estimate(30, 0)
assert capacity is not None and 0.99 < model.confidence <= 1.000001
# Independent formula for this synthetic known cost function.
rows = list(model.samples)
utilization = sum(r[1] for r in rows) * 30 * 8 / sum(r[4] for r in rows)
expected = (0.90 / 30 - 0.010 - 0.005) * 200000 * 30 * 8 / utilization
assert abs(capacity - expected) < 2
assert model.estimate(30, 3) is None
flat = ServiceBudget()
for n in range(50):
    flat.observe(n / 30, 2000, 0.020, 0.005, 0.003, 800000, 0)
assert flat.estimate(30, 0) is None
before = len(flat.samples)
flat.observe(2, 50000, 0.200, 0.005, 0.003, 800000, 0, keyframe=True)
assert len(flat.samples) == before

# Same low-motion workload: verify it settles without repeatedly returning to
# a rate which has already missed the frame budget. Frame cost includes a
# deterministic small payload variation, not a scripted 'success' threshold.
c = AdaptiveQuality(30, bitrate=1300000)
image = Image.new("RGB", (320, 240), "gray")
now = 0.0
rates = []
for n in range(1, 7201):
    size = int(c.bitrate * 0.85 / 30 / 8 * (1 + 0.08 * math.sin(n * 0.71)))
    cost = 0.017 + size / 160000
    now += max(1 / 30, cost)
    c.prepare(image, now)
    c.observe(now, n, cost, 0.007, size, encode_seconds=0.004, queued=1)
    rates.append(c.bitrate)
assert min(rates) >= 400000, "Repeated stale feedback collapsed quality"
assert max(rates[-1800:]) / min(rates[-1800:]) < 1.10, "Settled workload kept probing failed rates"
assert c.changes < 15, c.changes
assert c.reason in ("holding quality", "holding tested capacity", "settling after quality change")

# An unavoidable capture limit must not train a falsely low network ceiling.
c = AdaptiveQuality(30)
for n in range(100):
    c.prepare(image, n * 0.050)
    c.observe(n * 0.050, n, 0.055, 0.050, 1000, encode_seconds=0.002)
assert c.bitrate == 700000 and not c.unsafe_rates

# A slow host wake with cheap capture/encode/transfer is not receiver pressure.
# Previously each urgent cut cleared the sample window before it could reach
# the longer timing-limit check, repeatedly driving a cheap scene to 125kbps.
c = AdaptiveQuality(30)
for n in range(90):
    c.prepare(image, n / 15)
    c.observe(n / 15, n, 0.008, 0.002, 2500, encode_seconds=0.001, queued=1)
assert c.bitrate == 700000 and not c.unsafe_rates
assert c.reason == "frame timing limited"

# Test real wait helper with a deterministic timer, checking bounded sleeps,
# no Event.wait rounding and prompt cancellation/input maintenance.
real_time = pacing.time
clock = {"now": 0.0, "stopped": False, "sleeps": [], "ticks": 0}


class Stop:
    def is_set(self):
        return clock["stopped"]


def sleep(delay):
    assert 0 < delay <= 0.008
    clock["sleeps"].append(delay)
    clock["now"] += delay


def tick():
    clock["ticks"] += 1


try:
    pacing.time = SimpleNamespace(
        monotonic=lambda: math.floor(clock["now"] / 0.015625) * 0.015625,
        perf_counter=lambda: clock["now"],
        sleep=sleep,
    )
    assert pacing.wait_for_deadline(Stop(), 0.033333333, tick)
    assert abs(clock["now"] - 0.033333333) < 1e-9 and clock["ticks"] == 5
    clock["stopped"] = True
    assert not pacing.wait_for_deadline(Stop(), 1, tick)
finally:
    pacing.time = real_time
print(
    "PASS measured capacity/confidence, keyframe and capture exclusions, settled low-motion quality, bounded high-resolution waits and cancellation"
)
