"""Summarize host frame timings; acknowledgement is not panel display latency."""

import argparse
import json
from pathlib import Path
import statistics


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(path):
    frames = [
        record
        for line in path.read_text().splitlines()
        if (record := json.loads(line)).get("kind") == "frame"
    ]
    if not frames:
        raise ValueError("No frame timings in " + str(path))
    sessions = {frame["session"] for frame in frames}
    if len(sessions) != 1:
        raise ValueError("Analyze one connection session at a time: " + str(path))
    fields = (
        "capture_ms",
        "encode_ms",
        "rpc_transfer_ms",
        "credit_wait_ms",
        "wake_late_ms",
        "ack_interval_ms",
        "capture_to_ack_ms",
    )
    result = {
        "frames": len(frames),
        "elapsed_seconds": frames[-1]["elapsed"],
        "mean_displayed_fps": frames[-1]["displayed"] / frames[-1]["elapsed"],
        "clock_mhz": sorted({frame["clock_mhz"] for frame in frames}),
        "keyframes": sum(frame["keyframe"] for frame in frames),
        "frames_with_motion": sum((frame.get("motion_score") or 0) > 0.25 for frame in frames),
        "ack_gaps_above_50ms": sum((frame["ack_interval_ms"] or 0) > 50 for frame in frames),
        "host_timings": {},
    }
    for field in fields:
        values = [frame[field] for frame in frames if frame[field] is not None]
        if not values:
            result["host_timings"][field] = None
            continue
        result["host_timings"][field] = {
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {path.name: summarize(path) for path in args.traces}
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
