import argparse
import json
import secrets
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from transport import Bridge
from capture import create_capture, TimingLease
from encoder import Encoder, test_image
from mirror import exchange_control
from wire import HELLO, CAPS, POLL, BULK, CAP_BULK, OK, fragments, acknowledgement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["h264", "mpeg4"], default="h264")
    parser.add_argument("--test-pattern", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("build/latency.json"))
    parser.add_argument("--mpeg4-q", type=int, default=1)
    args = parser.parse_args()
    stop = threading.Event()
    timer = TimingLease()
    camera = create_capture(argparse.Namespace(region=None, monitor=1, capture="dxgi"))
    bridge = Bridge(stop, "windows")
    session = secrets.randbits(32) or 1
    times = []
    try:
        state = exchange_control(bridge, HELLO, session)
        caps = exchange_control(bridge, CAPS, session)
        assert state["status"] == OK and caps["received"] & CAP_BULK
        encoder = Encoder(30, 16, mode=args.mode, quantizer=args.mpeg4_q)
        for i in range(30):
            started = time.perf_counter()
            output = encoder.encode(test_image(i) if args.test_pattern else camera.grab())
            data, key = output[:2]
            flags = output[2] if len(output) > 2 else 0
            with bridge.connection():
                state = acknowledgement(
                    bridge.batch(
                        list(fragments(session, i + 1, data, key, state["limit"], BULK | flags))
                    ),
                    session,
                )
                assert state["status"] == OK
            while state["displayed"] < i + 1:
                state = exchange_control(bridge, POLL, session)
                assert time.perf_counter() - started < 2
            times.append((time.perf_counter() - started) * 1000)
            time.sleep(0.07)
        result = dict(
            samples=times,
            mean_ms=statistics.mean(times),
            median_ms=statistics.median(times),
            p95_ms=sorted(times)[28],
            description="Capture start to calculator display confirmation; includes poll overhead, not glass-to-glass panel timing",
        )
        result.update(
            mode=args.mode,
            test_pattern=args.test_pattern,
            mpeg4_q=args.mpeg4_q if args.mode == "mpeg4" else None,
        )
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    finally:
        bridge.close()
        camera.close()
        timer.close()


if __name__ == "__main__":
    main()
