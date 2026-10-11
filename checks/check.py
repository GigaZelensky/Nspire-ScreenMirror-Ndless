from pathlib import Path
import json
import shutil
import struct
import subprocess
import sys
import av
import argparse
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "vendor/ndvideo"
sys.path.insert(0, str(ROOT / "pc"))
from encoder import Encoder, test_image
from wire import fragments, control, HELLO, acknowledgement, ACK


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", default="ultrafast")
    parser.add_argument("--crf", type=int, default=20)
    args = parser.parse_args()
    out = ROOT / "build/checks" / sys.platform
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    codec = Encoder(20, args.crf, preset=args.preset)
    for i in range(60):
        data, key = codec.encode(test_image(i))
        frames.append((data, key))
    assert sum(key for _, key in frames) == 1
    for limit in (254, 1440):
        fixture = out / f"packets-{limit}.bin"
        packets = [control(HELLO, 73)]
        for sequence, (data, key) in enumerate(frames, 1):
            packets.extend(fragments(73, sequence, data, key, limit))
        with fixture.open("wb") as f:
            for packet in packets:
                f.write(struct.pack("<I", len(packet)) + packet)
        cc = shutil.which("gcc")
        assert cc
        exe = out / "receiver.exe"
        sources = [
            ROOT / "checks/receiver.c",
            ROOT / "calc/decoder.c",
            *sorted((REPO / "src/codecs/h264bsd").glob("*.c")),
        ]
        dependencies = [*sources, *list((ROOT / "calc").glob("*.h"))]
        if not exe.exists() or max(p.stat().st_mtime for p in dependencies) > exe.stat().st_mtime:
            subprocess.run(
                [
                    cc,
                    "-std=gnu99",
                    "-O2",
                    "-DNDEBUG",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I" + str(ROOT / "calc"),
                    "-I" + str(REPO / "src/codecs"),
                    "-I" + str(REPO / "src"),
                    "-Wl,--gc-sections",
                    *map(str, sources),
                    "-o",
                    str(exe),
                ],
                check=True,
            )
        subprocess.run([str(exe), str(fixture), str(out / f"pixels-{limit}.rgb565")], check=True)
    a = (out / "pixels-254.rgb565").read_bytes()
    b = (out / "pixels-1440.rgb565").read_bytes()
    assert a == b
    assert len(a) == 60 * 320 * 240 * 2
    # Compare the native decoder against FFmpeg decoding the same access units.
    decoder = av.CodecContext.create("h264", "r")
    reference = []
    for data, _ in frames:
        reference.extend(decoder.decode(av.Packet(data)))
    reference.extend(decoder.decode(None))
    assert len(reference) == 60
    actual = np.frombuffer(a, dtype="<u2").reshape(60, 240, 320)
    rgb = np.stack(
        (
            ((actual >> 11) & 31) * 255 // 31,
            ((actual >> 5) & 63) * 255 // 63,
            (actual & 31) * 255 // 31,
        ),
        axis=-1,
    ).astype(np.int32)
    differences = []
    for i, image in enumerate(reference):
        target = image.to_ndarray(format="rgb24").astype(np.int32)
        differences.append(float(np.abs(rgb[i] - target).mean()))
    assert max(differences) < 8, differences
    reply = ACK.pack(b"NDA1", 1, 0, 2, 1, 73, 0, 0, 1440, 0, 0)
    assert acknowledgement(reply, 73)["limit"] == 1440
    for bad in [
        reply[:12],
        b"BAD!" + reply[4:],
        ACK.pack(b"NDA1", 1, 0, 3, 1, 73, 0, 0, 1440, 0, 0),
    ]:
        try:
            acknowledgement(bad, 73)
        except IOError:
            pass
        else:
            raise AssertionError("Malformed reply accepted")
    (out / "sample.h264").write_bytes(b"".join(data for data, _ in frames))
    # Cross-decoder colour/motion evidence, suitable for visual inspection.
    from PIL import Image

    Image.fromarray(np.clip(rgb[30], 0, 255).astype("uint8")).resize(
        (640, 480), Image.Resampling.NEAREST
    ).save(out / "decoded-preview.png")
    result = {
        "frames": 60,
        "keyframes": sum(key for _, key in frames),
        "bytes": sum(len(data) for data, _ in frames),
        "bitrate_kbps": sum(len(data) for data, _ in frames) * 8 / 3 / 1000,
        "cross_decoder_mean_abs_rgb_error_max": max(differences),
        "packet_limits": [254, 1440],
        "physical_usb_tested": False,
    }
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
