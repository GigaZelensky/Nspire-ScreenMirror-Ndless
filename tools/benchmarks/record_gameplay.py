"""Record only the mirrored 320x240 monitor image, without audio, for replay tests."""

import argparse
import json
import sys
import time
from pathlib import Path
from fractions import Fraction
import av

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from capture import create_capture, TimingLease


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=40)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    camera = create_capture(argparse.Namespace(monitor=1, region=None, capture="dxgi"))
    timing = TimingLease()
    frames = 0
    last_pts = -1
    started = time.monotonic()
    try:
        with av.open(str(args.output), "w") as output:
            video = output.add_stream("ffv1", rate=30)
            video.width = 320
            video.height = 240
            video.pix_fmt = "bgr0"
            while time.monotonic() - started < args.seconds:
                elapsed = time.monotonic() - started
                pts = int(elapsed * 30)
                if pts <= last_pts:
                    time.sleep(max(0.001, (last_pts + 1) / 30 - elapsed))
                    continue
                image = camera.grab()
                frame = av.VideoFrame.from_image(image)
                frame.pts = pts
                frame.time_base = Fraction(1, 30)
                for packet in video.encode(frame):
                    output.mux(packet)
                frames += 1
                last_pts = pts
            for packet in video.encode():
                output.mux(packet)
    finally:
        camera.close()
        timing.close()
    result = {
        "frames": frames,
        "duration_seconds": time.monotonic() - started,
        "resolution": [320, 240],
        "audio": False,
        "lossless": True,
        "bytes": args.output.stat().st_size,
    }
    args.output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(result)


if __name__ == "__main__":
    main()
