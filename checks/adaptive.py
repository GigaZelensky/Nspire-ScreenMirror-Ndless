"""Verify controller response, bounded recovery and real decoder rate changes."""

from pathlib import Path
import sys
import struct
import subprocess
import json
import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from adaptive import AdaptiveQuality
from encoder import Encoder, test_image
from wire import fragments, control, HELLO
from PIL import Image


def simulate(controller, frames, service, start=0):
    result = []
    now = start
    for count in range(1, frames + 1):
        now += max(1 / controller.fps, service)
        value = controller.observe(now, count, service, 0.004)
        if value is not None:
            result.append((now, value))
    return now, result


c = AdaptiveQuality(30)
t, drops = simulate(c, 90, 0.065)
assert drops and drops[0][0] < 1.2 and c.bitrate == c.minimum, (drops, c.bitrate)
before = c.bitrate
t, recover = simulate(c, 210, 0.012, t)
assert recover and recover[0][0] - drops[-1][0] >= 0.75 and c.minimum < c.bitrate <= c.maximum, (
    recover
)
c = AdaptiveQuality(30)
_, changes = simulate(c, 120, 0.031)
assert not changes, "Near-capacity steady stream oscillated"
c = AdaptiveQuality(30)
_, changes = simulate(c, 1200, 0.006)
assert c.bitrate == c.maximum and all(b <= c.maximum for _, b in changes)
c = AdaptiveQuality(30)
_, changes = simulate(c, 35, 1 / 28)
assert not changes, "A mild 28 FPS dip unnecessarily reduced detail"
c = AdaptiveQuality(30)
c.bitrate = 2000000
c.safe_motion_bitrate = 400000
black = Image.new("RGB", (320, 240))
white = Image.new("RGB", (320, 240), "white")
assert c.prepare(black, 0) is None and c.prepare(white, 0.04) is None
assert c.prepare(black, 0.08) == 400000 and c.predictions == 1
c.bitrate = 2000000
for n in range(8):
    assert c.prepare(black, 0.12 + n * 0.04) is None
assert c.prepare(white, 0.5) is None and c.prepare(white, 0.54) is None, (
    "An isolated scene cut lowered quality"
)
print(
    "PASS rapid overload response, bounded bitrate, fast easy-scene recovery, mild-dip tolerance and stable near-capacity behavior"
)
print("PASS learned motion budget applies before encoding; isolated cuts retain quality")
c = AdaptiveQuality(30)
c.bitrate = 2000000
c.safe_rates = {3: 400000}
grey = Image.new("RGB", (320, 240), (4, 4, 4))
assert c.prepare(black, 0) is None and c.prepare(grey, 0.033) is None
assert c.prepare(black, 0.066) is None and c.bitrate == 2000000, (
    "Slow movement inherited a fast-motion quality cap"
)
assert c.prepare(white, 0.10) is None and c.prepare(black, 0.133) == 400000, (
    "Slow-to-fast movement bypassed prediction"
)
c = AdaptiveQuality(30)
c.bitrate = 1600000
c.motion_band = 3
c.motion_average = 15
c.safe_rates = {3: 1600000}
c.safe_levels = {3: 15}
c.previous_image = black.resize((32, 24)).convert("L")
bright = Image.new("RGB", (320, 240), (36, 36, 36))
assert c.prepare(bright, 0.033) is None
assert c.prepare(black, 0.066) < 1000000, (
    "Speed increase within the same motion band bypassed prediction"
)
c = AdaptiveQuality(30)
for n in range(1, 41):
    assert c.observe(n * 0.06, n, 0.06, 0.05) is None
assert c.bitrate == 700000 and c.reason == "desktop capture limited"
c = AdaptiveQuality(30)
for n in range(1, 31):
    c.observe(n / 30, n, 0.020, 0.004)
c.motion_band = 3
changes = []
for n in range(1, 16):
    value = c.observe(1 + n * 0.06, 30 + n, 0.06, 0.004)
    if value is not None:
        changes.append((n * 0.06, value))
assert changes and changes[0][0] < 0.6, changes
print(
    "PASS slow-to-fast prediction, capture-stall quality preservation and sub-600ms overload response"
)

# Slow-looking video can still saturate decoding/USB. After a real slowdown,
# brief headroom must not immediately repeat the same large bitrate overshoot.
c = AdaptiveQuality(30, bitrate=1500000)
now = 0.0
displayed = 0
for n in range(60):
    now += 0.045
    displayed += 1
    c.prepare(black, now)
    c.observe(now, displayed, 0.045, 0.004, 3000)
reduced = c.bitrate
assert reduced < 1500000
increases = []
for n in range(150):
    now += 1 / 30
    displayed += 1
    c.prepare(black, now)
    before = c.bitrate
    changed = c.observe(now, displayed, 0.020, 0.004, 3000)
    if changed is not None and changed > before:
        increases.append((now, changed / before))
assert increases and all(factor <= 1.1 for _, factor in increases), increases
before = c.bitrate
for n in range(100):
    now += 1 / 30
    displayed += 1
    c.prepare(black, now)
    c.observe(now, displayed, 0.008, 0.004, 16)
assert c.bitrate >= before * 1.5, "Easy scene kept the overload quality restriction"
print("PASS recent overload recovers gradually; tiny static frames restore detail quickly")

out = ROOT / "build/checks/adaptive"
out.mkdir(parents=True, exist_ok=True)
encoder = Encoder(30, 14, preset="fast", bitrate=1000000)
frames = []
for n in range(120):
    if n == 40:
        encoder.set_bitrate(200000)
    if n == 80:
        encoder.set_bitrate(700000)
    frames.append(encoder.encode(test_image(n)))
assert sum(key for data, key in frames) == 1, "Quality change restarted the stream"
fixture = out / "packets.bin"
with fixture.open("wb") as f:
    packets = [control(HELLO, 73)]
    for n, (data, key) in enumerate(frames, 1):
        packets.extend(fragments(73, n, data, key, 1440))
    for p in packets:
        f.write(struct.pack("<I", len(p)) + p)
subprocess.run(
    [
        str(ROOT / "build/checks" / sys.platform / "receiver.exe"),
        str(fixture),
        str(out / "pixels.rgb565"),
    ],
    check=True,
)
decoder = av.CodecContext.create("h264", "r")
reference = []
for data, key in frames:
    reference.extend(decoder.decode(av.Packet(data)))
reference.extend(decoder.decode(None))
assert len(reference) == 120
pixels = np.fromfile(out / "pixels.rgb565", dtype="<u2").reshape(120, 240, 320).astype(np.int32)
rgb = np.stack(
    (
        ((pixels >> 11) & 31) * 255 // 31,
        ((pixels >> 5) & 63) * 255 // 63,
        (pixels & 31) * 255 // 31,
    ),
    axis=-1,
)
error = max(
    float(np.abs(rgb[i] - frame.to_ndarray(format="rgb24").astype(np.int32)).mean())
    for i, frame in enumerate(reference)
)
assert error < 8, error
(out / "results.json").write_text(
    json.dumps(
        {
            "frames": 120,
            "quality_changes": 2,
            "keyframes": 1,
            "max_mean_rgb_error": error,
            "physical_usb_tested": False,
        },
        indent=2,
    )
    + "\n"
)
print(
    "PASS live ABR changes without extra IDRs; 120 frames through production decoder match FFmpeg",
    error,
)

# Property readback alone cannot show that x264 actually changed rate control.
# A sustained textured workload must emit substantially less data after a drop.
rng = np.random.default_rng(73)
images = [
    Image.fromarray(rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)) for unused in range(12)
]
encoder = Encoder(30, 14, preset="ultrafast", bitrate=1000000)
traffic = []
for rate in (1000000, 200000, 1000000):
    encoder.set_bitrate(rate)
    sizes = []
    for n in range(100):
        sizes.append(len(encoder.encode(images[n % 12])[0]))
    traffic.append(sum(sizes[50:]))
assert traffic[1] < traffic[0] * 0.4 and traffic[2] > traffic[1] * 2, traffic
print("PASS actual traffic follows down/up bitrate changes", traffic)
