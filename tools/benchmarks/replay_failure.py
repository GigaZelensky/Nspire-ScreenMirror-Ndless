"""Replay an opt-in failure capture through FFmpeg and the actual C receiver."""

from pathlib import Path
import argparse
import json
import os
import struct
import subprocess
import sys

import av

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from wire import control, fragments, HELLO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stream", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/failure-replay")
    args = parser.parse_args()
    if args.stream.stat().st_size > 16 * 1024 * 1024:
        parser.error("Capture exceeds its 16 MiB limit")
    data = args.stream.read_bytes()
    metadata = json.loads(args.stream.with_suffix(".json").read_text())
    frames, session = metadata["frames"], metadata["session"]
    if not 0 < session <= 0xFFFFFFFF or not 0 < len(frames) <= 1800 or not frames[0]["key"]:
        parser.error("Invalid capture metadata or missing initial keyframe")
    if sum(frame["bytes"] for frame in frames) != len(data):
        parser.error("Frame lengths do not match the captured stream")
    args.output.mkdir(parents=True, exist_ok=True)
    decoder = av.CodecContext.create("h264", "r")
    decoded, offset = 0, 0
    fixture = args.output / "packets.bin"
    with fixture.open("wb") as file:
        hello = control(HELLO, session)
        file.write(struct.pack("<I", len(hello)) + hello)
        for frame in frames:
            if not 0 < frame["frame"] < 0xFFFFFFFF or not 0 < frame["bytes"] <= 65536:
                parser.error("Invalid captured frame")
            packet_data = data[offset : offset + frame["bytes"]]
            offset += frame["bytes"]
            decoded += len(decoder.decode(av.Packet(packet_data)))
            fnv = 2166136261
            for byte in packet_data:
                fnv = ((fnv ^ byte) * 16777619) & 0xFFFFFFFF
            print(f"frame={frame['frame']} bytes={len(packet_data)} input_fnv32={fnv:08x}")
            for packet in fragments(session, frame["frame"], packet_data, frame["key"], 1440):
                file.write(struct.pack("<I", len(packet)) + packet)
    decoded += len(decoder.decode(None))
    print(f"FFmpeg decoded {decoded}/{len(frames)} pictures")
    if decoded != len(frames):
        raise RuntimeError("Incomplete FFmpeg decode")
    binary = ROOT / "build/checks" / sys.platform / "receiver.exe"
    if not binary.exists():
        parser.error("Run checks/check.py first to build the C receiver harness")
    result = subprocess.run([str(binary), str(fixture), os.devnull], capture_output=True, text=True)
    print(result.stdout, result.stderr, end="")
    (args.output / "result.json").write_text(
        json.dumps(
            {
                "frames": len(frames),
                "ffmpeg_frames": decoded,
                "receiver_returncode": result.returncode,
                "receiver_stdout": result.stdout,
                "receiver_stderr": result.stderr,
            },
            indent=2,
        )
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
