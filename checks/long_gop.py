"""Long reference chains and their next IDR decode through the actual C receiver."""

from pathlib import Path
import subprocess
import struct
import sys
import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from encoder import Encoder, test_image
from wire import control, fragments, HELLO

out = ROOT / "build/checks/long-gop"
out.mkdir(parents=True, exist_ok=True)
encoder = Encoder(30, 14, key_seconds=30)
frames = [encoder.encode(test_image(i)) for i in range(930)]
assert [i for i, (_, key) in enumerate(frames) if key] == [0, 900]
fixture = out / "packets.bin"
with fixture.open("wb") as file:
    packets = [control(HELLO, 73)]
    for sequence, (data, key) in enumerate(frames, 1):
        packets.extend(fragments(73, sequence, data, key, 1440))
    for packet in packets:
        file.write(struct.pack("<I", len(packet)) + packet)
pixels = out / "pixels.rgb565"
subprocess.run(
    [str(ROOT / "build/checks" / sys.platform / "receiver.exe"), str(fixture), str(pixels)],
    check=True,
)
actual = np.memmap(pixels, dtype="<u2", mode="r", shape=(930, 240, 320))
decoder = av.CodecContext.create("h264", "r")
decoded = 0
for data, key in frames:
    for picture in decoder.decode(av.Packet(data)):
        if decoded in (0, 450, 899, 900, 929):
            values = actual[decoded].astype(np.int32)
            rgb = np.stack(
                (
                    ((values >> 11) & 31) * 255 // 31,
                    ((values >> 5) & 63) * 255 // 63,
                    (values & 31) * 255 // 31,
                ),
                axis=-1,
            )
            error = float(np.abs(rgb - picture.to_ndarray(format="rgb24").astype(np.int32)).mean())
            assert error < 8, error
        decoded += 1
assert decoded == 930
print(
    "PASS 930 frames across a 30-second reference chain and next IDR match FFmpeg through production C receiver"
)
