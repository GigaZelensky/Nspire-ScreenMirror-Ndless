"""Rounded timer wakes do not accumulate drift; static scenes do not thrash ABR."""

from pathlib import Path
import sys
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from pacing import next_deadline
from adaptive import AdaptiveQuality
import math

deadline = now = 0.0
starts = []
for unused in range(300):
    if now < deadline:
        now = math.ceil(deadline / 0.016) * 0.016
    starts.append(now)
    now += 0.014
    deadline = next_deadline(deadline, now, 30)
assert 29.5 < (len(starts) - 1) / (starts[-1] - starts[0]) < 30.5
assert next_deadline(0, 0.200, 30) == 0.200

controller = AdaptiveQuality(30, bitrate=1000000)
image = Image.new("RGB", (320, 240), "white")
for n in range(200):
    now = n / 25
    controller.prepare(image, now)
    controller.observe(now, n, 0.012, 0.003, 24)
assert controller.bitrate == 1000000 and controller.changes == 0
assert controller.reason == "frame timing limited"
print(
    "PASS rounded timer wakes retain target FPS; stale deadlines reset; static low-traffic timing noise does not lower quality"
)
