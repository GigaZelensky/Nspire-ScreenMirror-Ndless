"""Physical USB comparison with an isolated 8ms capture cost and a 24fps source clock."""

import argparse
import json
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from sweep import sources, Borrowed
from transport import Bridge
import mirror


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frames = sources(args.video)["video"]
    results = []
    bridge = Bridge(threading.Event(), "windows")
    try:
        for preset, q, fps, pipeline in [
            ("ultrafast", 14, 30, False),
            ("ultrafast", 14, 30, True),
            ("ultrafast", 14, 40, False),
            ("ultrafast", 14, 40, True),
            ("fast", 10, 30, False),
            ("fast", 10, 30, True),
        ]:
            anchor = [0.0]
            records = []
            messages = []
            stop = threading.Event()

            class Camera:
                name = "24fps motion / simulated 8ms capture"
                region = dict(left=0, top=0, width=2560, height=1440)

                def __init__(self, unused):
                    pass

                def grab(self):
                    time.sleep(0.008)
                    index = (
                        int((time.monotonic() - anchor[0]) * 24) % len(frames) if anchor[0] else 0
                    )
                    return frames[index]

                def close(self):
                    pass

            mirror.create_capture = Camera
            options = argparse.Namespace(
                test_pattern=False,
                fps=fps,
                crf=q,
                preset=preset,
                clock=492,
                mode="h264",
                input=False,
                duration=12,
                pipeline=pipeline,
                backend="windows",
                library=None,
                packet_size=None,
                region=None,
                monitor=1,
            )

            def report(message):
                messages.append(message)
                if message.startswith("Connected /"):
                    anchor[0] = time.monotonic()

            print("CASE", preset, q, fps, pipeline, flush=True)
            watchdog = threading.Timer(90, stop.set)
            watchdog.daemon = True
            watchdog.start()
            mirror.stream(
                options,
                stop,
                report,
                bridge_factory=lambda stop, *unused: Borrowed(bridge, stop),
                stats_report=records.append,
            )
            watchdog.cancel()
            if not records:
                raise RuntimeError(messages)
            record = records[-1]
            record["messages"] = [m for m in messages if "Retrying" in m]
            results.append(record)
            args.output.write_text(
                json.dumps(
                    dict(
                        method="Physical USB, source follows a 24fps wall clock, 8ms capture delay is simulated to isolate overlap; not a live desktop capture measurement",
                        cases=results,
                    ),
                    indent=2,
                )
                + "\n"
            )
            print(json.dumps(record), flush=True)
            if record["messages"]:
                raise RuntimeError("Interrupted comparison")
    finally:
        bridge.stop = threading.Event()
        bridge.close()


if __name__ == "__main__":
    main()
