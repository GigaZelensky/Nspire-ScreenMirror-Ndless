"""Summarize complete live RAM/SRAM phases, including borrowing overhead."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def summarize(rows, phase_seconds=30):
    sessions = defaultdict(list)
    for row in rows:
        if row.get("session"):
            sessions[row["session"]].append(row)
    output = []
    for session, records in sessions.items():
        starts = [row for row in records if row.get("kind") == "sram_phase"]
        snapshots = [row for row in records if row.get("kind") == "receiver_profile"]
        if not starts:
            continue
        boundaries = starts[:]
        if snapshots and snapshots[-1]["elapsed"] > starts[-1]["elapsed"]:
            boundaries.append(snapshots[-1])
        for start, end in zip(boundaries, boundaries[1:]):
            duration = end["elapsed"] - start["elapsed"]
            if duration < phase_seconds * 0.95:
                continue
            before, after = start["profile"], end["profile"]
            a, b = before["sram_tables"], after["sram_tables"]
            mode = "sram" if start["enabled"] else "ram"
            frames = b[mode + "_frames"] - a[mode + "_frames"]
            all_frames = after["frames"] - before["frames"]
            if frames <= 0 or not b["hz"] or b["hz"] != a["hz"]:
                continue
            conversion = (b[mode + "_convert_ticks"] - a[mode + "_convert_ticks"]) / b["hz"]
            overhead = (
                sum(
                    b[name + "_ticks"] - a[name + "_ticks"]
                    for name in ("setup", "restore", "verify")
                )
                / b["hz"]
            )
            samples = [
                row
                for row in records
                if row.get("kind") == "frame"
                and start["elapsed"] + 3 <= row["elapsed"] < end["elapsed"]
            ]

            def mean(name):
                return statistics.mean(row[name] for row in samples) if samples else None

            output.append(
                dict(
                    session=session,
                    phase=start["phase"],
                    mode=mode,
                    duration=duration,
                    path_frames=frames,
                    displayed_frames=all_frames,
                    fps=all_frames / duration,
                    conversion_ms=conversion * 1000 / frames,
                    loan_ms=overhead * 1000 / frames,
                    total_colour_ms=(conversion + overhead) * 1000 / frames,
                    busy_skips=b["busy_skips"] - a["busy_skips"],
                    restore_errors=b["restore_errors"] - a["restore_errors"],
                    motion=mean("motion_score"),
                    bitrate=mean("bitrate"),
                    frame_bytes=mean("bytes"),
                    transfer_ms=mean("rpc_transfer_ms"),
                    credit_wait_ms=mean("credit_wait_ms"),
                )
            )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--phase-seconds", type=float, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    lines = args.metrics.read_text().splitlines()
    for index, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A live buffered writer can leave its final record incomplete.
            if index != len(lines) - 1:
                raise
    result = summarize(rows, args.phase_seconds)
    for phase in result:
        print(
            f"session={phase['session']:x} phase={phase['phase']} {phase['mode']:4} "
            f"frames={phase['path_frames']} fps={phase['fps']:.2f} "
            f"colour={phase['conversion_ms']:.3f}ms loan={phase['loan_ms']:.3f}ms "
            f"net={phase['total_colour_ms']:.3f}ms motion={phase['motion']:.2f} "
            f"bitrate={phase['bitrate']:.0f} skips={phase['busy_skips']} errors={phase['restore_errors']}"
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
