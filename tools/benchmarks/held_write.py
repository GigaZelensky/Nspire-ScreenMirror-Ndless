"""Hold one NNSE reply acknowledgement to reproduce the pending-write unplug path.

Receiver remains on its waiting screen. A fresh Mirror HELLO receives a valid
reply, but its outer acknowledgement is withheld until physical unplugging.
Renew the held write every 45 seconds before its native timeout, for at most
five minutes. This gives the operator time to act without mistaking an expired
hold for a successful cancellation. Always release ACKs when ending connected.
"""

from pathlib import Path
import argparse
import json
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pc"))
from calculator_files import connected_calculator
from nspire_usb import OUTER
from wire import HELLO, control, acknowledgement


class HoldAck:
    def __init__(self, io):
        self.io = io
        self.withheld = []
        self.holding = True

    def write(self, data):
        if self.holding and len(data) == OUTER.size and data[1] == 0x84:
            if len(self.withheld) >= 64:
                raise RuntimeError("Held acknowledgement limit reached")
            self.withheld.append(data)
        else:
            self.io.write(data)

    def read(self, *args):
        return self.io.read(*args)

    def release(self):
        self.holding = False
        for message in self.withheld:
            self.io.write(message)
        self.withheld.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--stop-file", type=Path, help="End the connected probe when this file appears"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output file already exists; choose a new evidence filename")
    result = {"started": time.time(), "held_acknowledgements": 0, "unplugged": False, "cycles": 0}
    try:
        with connected_calculator() as client:
            protocol = client.protocol
            if not protocol.cx2:
                raise RuntimeError("This probe requires a CX II NNSE transport")
            held = HoldAck(protocol.io)
            protocol.io = held
            try:
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline and not (
                    args.stop_file and args.stop_file.exists()
                ):
                    session = secrets.randbits(32) or 1
                    held.holding = True
                    reply = protocol.exchange(control(HELLO, session))
                    info = acknowledgement(reply, session)
                    if not held.withheld or info["status"] != 0:
                        raise RuntimeError("No valid Mirror reply acknowledgement was held")
                    result["cycles"] += 1
                    print(
                        "PENDING WRITE: reply received; its NNSE acknowledgement is withheld.",
                        flush=True,
                    )
                    print("Unplug USB now, then press Ctrl+Esc on the calculator.", flush=True)
                    cycle_end = min(deadline, time.monotonic() + 45)
                    while time.monotonic() < cycle_end and not (
                        args.stop_file and args.stop_file.exists()
                    ):
                        try:
                            protocol.receive_outer(time.monotonic() + 0.25)
                            # Retransmitted replies must not fill the normal
                            # stream receive queue while their ACK is held.
                            while protocol.incoming:
                                payload = protocol.handle_inner(protocol.incoming.popleft())
                                if payload is not None:
                                    acknowledgement(payload, session)
                        except TimeoutError:
                            continue
                        except OSError as error:
                            if getattr(error, "winerror", None) not in (22, 433, 1167):
                                raise
                            result["unplugged"] = True
                            result["disconnect_error"] = str(error)
                            print("USB disconnected; no further requests sent.", flush=True)
                            break
                    if result["unplugged"]:
                        break
                    result["held_acknowledgements"] += len(held.withheld)
                    held.release()
                    protocol.end()
                    if time.monotonic() < deadline and not (
                        args.stop_file and args.stop_file.exists()
                    ):
                        time.sleep(0.04)
                        protocol.ready = False
                        protocol.begin()
                if not result["unplugged"]:
                    print("No unplug within five minutes; ending the probe.", flush=True)
            finally:
                result["held_acknowledgements"] += len(held.withheld)
                if not result["unplugged"]:
                    held.release()
                    protocol.end()
    except Exception as error:
        result["failure"] = str(error)
        raise
    finally:
        result["finished"] = time.time()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(result, file, indent=2)
            file.write("\n")


if __name__ == "__main__":
    main()
