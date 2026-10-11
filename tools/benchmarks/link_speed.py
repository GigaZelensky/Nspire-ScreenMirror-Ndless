import json
import secrets
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from transport import Bridge
from mirror import exchange_control
from wire import HELLO, CAPS, PROBE, BULK, CAP_PROBE, OK, fragments, acknowledgement


def main():
    stop = threading.Event()
    bridge = Bridge(stop, "windows")
    session = secrets.randbits(32) or 1
    results = []
    try:
        state = exchange_control(bridge, HELLO, session)
        assert state["status"] == OK
        caps = exchange_control(bridge, CAPS, session)
        assert caps["received"] & CAP_PROBE
        print("Receiver clock MHz:", caps["rejected"], flush=True)
        limit = state["limit"]
        payload = bytes(range(256)) * 256
        for sample in range(3):
            started = time.perf_counter()
            sent = 0
            requests = 0
            for frame in range(8):
                packets = list(fragments(session, frame + 1, payload, False, limit, BULK, PROBE))
                with bridge.connection():
                    reply = acknowledgement(bridge.batch(packets), session)
                    assert reply["status"] == OK
                sent += len(payload)
                requests += len(packets)
            elapsed = time.perf_counter() - started
            result = dict(
                sample=sample,
                payload_bytes=sent,
                seconds=elapsed,
                payload_MiB_s=sent / elapsed / 1048576,
                payload_mbps=sent * 8 / elapsed / 1e6,
                packets=requests,
            )
            results.append(result)
            print(json.dumps(result), flush=True)
        Path("build/link-speed.json").write_text(json.dumps(results, indent=2) + "\n")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
