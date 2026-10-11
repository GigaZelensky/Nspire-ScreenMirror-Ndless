"""Replay identical captured H.264 bytes on the calculator at selected clocks."""

import argparse
import json
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from transport import Bridge
from wire import (
    HELLO,
    CAPS,
    CLOCK,
    STATS,
    STREAM,
    POLL,
    OK,
    BUSY,
    CAP_CLOCK,
    CAP_SRAM_TABLES,
    BULK,
    ReceiverClosing,
    acknowledgement,
    control,
    fragments,
)
from mirror import exchange_control


def repeated_frames(frames, repeats):
    span = frames[-1]["frame"] - frames[0]["frame"] + 1
    if not 1 <= repeats <= 30 or frames[-1]["frame"] + (repeats - 1) * span >= 2**32:
        raise ValueError("Invalid repeat count or frame number overflow")
    for cycle in range(repeats):
        for frame in frames:
            yield dict(frame, frame=frame["frame"] + cycle * span)


def replay(data, frames, clock, fps, stop, bridge_factory=Bridge, repeats=1):
    result = dict(clock=clock, completed=0, expected=len(frames) * repeats, failure=None)
    result["closed_by_receiver"] = False
    replay_started = None
    bridge = bridge_factory(stop)
    session = secrets.randbits(32) or 1
    try:
        state = exchange_control(bridge, HELLO, session)
        caps = exchange_control(bridge, CAPS, session)
        if state["status"] != OK or not caps["received"] & CAP_CLOCK:
            raise IOError("Receiver is not ready or does not support clock control")
        changed = exchange_control(bridge, CLOCK, session, clock)
        if changed["status"] != OK:
            raise IOError("Receiver rejected the requested clock")
        for _ in range(30):
            if stop.wait(0.05):
                raise InterruptedError("Stopped")
            current = exchange_control(bridge, CAPS, session)
            if current["status"] != BUSY:
                break
        if current["status"] != OK or current["rejected"] != clock:
            raise IOError("Receiver did not confirm the requested clock")
        with bridge.connection():

            def command(kind, value=0):
                return acknowledgement(bridge.exchange(control(kind, session, value)), session)

            if caps["received"] & CAP_SRAM_TABLES:
                selected = command(STATS, 3)
                if selected["status"] != OK or selected["profile"]["sram_tables"]["enabled"]:
                    raise IOError("Receiver did not disable SRAM loans")
            command(STATS, 1)
            started = command(STREAM)
            if started["status"] != OK:
                raise IOError("Receiver rejected persistent transfer")
            offset = 0
            replay_started = due = time.monotonic()
            for frame in repeated_frames(frames, repeats):
                while not state["credit"]:
                    if stop.wait(0.001):
                        raise InterruptedError("Stopped")
                    state = command(POLL)
                    if state["status"] != OK:
                        raise IOError(f"Receiver requested reset before frame {frame['frame']}")
                if stop.wait(max(0, due - time.monotonic())):
                    raise InterruptedError("Stopped")
                if offset == len(data):
                    offset = 0
                payload = data[offset : offset + frame["bytes"]]
                offset += frame["bytes"]
                result["submitted_frame"] = frame["frame"]
                state = acknowledgement(
                    bridge.batch(
                        list(
                            fragments(
                                session, frame["frame"], payload, frame["key"], caps["limit"], BULK
                            )
                        )
                    ),
                    session,
                )
                if state["status"] != OK:
                    result["receiver"] = {
                        k: state[k] for k in ("status", "accepted", "displayed", "need_key")
                    }
                    raise IOError(f"Receiver reset at captured frame {frame['frame']}")
                result["completed"] += 1
                due += 1 / fps
                if due < time.monotonic() - 1 / fps:
                    due = time.monotonic()
            deadline = time.monotonic() + 3
            while state["displayed"] < state["accepted"]:
                state = command(POLL)
                if state["status"] != OK or time.monotonic() > deadline:
                    raise IOError("Receiver did not display the final captured frame")
                if stop.wait(0.001):
                    raise InterruptedError("Stopped")
            result["duration"] = time.monotonic() - replay_started
            result["profile"] = command(STATS, 2).get("profile")
            bridge.pause_session(session)
    except ReceiverClosing:
        result["closed_by_receiver"] = True
    except (OSError, InterruptedError) as error:
        result["failure"] = str(error)
    finally:
        if replay_started is not None:
            result["duration"] = time.monotonic() - replay_started
        bridge.close()
    return result


def load_capture(path):
    if not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("Capture must contain 1 byte to 16 MiB")
    data = path.read_bytes()
    frames = json.loads(path.with_suffix(".json").read_text())["frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1800:
        raise ValueError("Capture must contain 1 to 1800 frames")
    previous = 0
    for frame in frames:
        if (
            not isinstance(frame, dict)
            or type(frame.get("bytes")) is not int
            or not 1 <= frame["bytes"] <= 65536
            or type(frame.get("frame")) is not int
            or not previous < frame["frame"] < 2**32
            or type(frame.get("key")) is not bool
        ):
            raise ValueError("Invalid frame metadata")
        previous = frame["frame"]
    if not frames[0]["key"] or sum(f["bytes"] for f in frames) != len(data):
        raise ValueError("Capture is incomplete or lacks an initial keyframe")
    return data, frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--clocks", type=int, nargs="+", default=[396, 492])
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not 1 <= args.rounds <= 8
        or not 1 <= args.fps <= 60
        or not 1 <= args.repeat <= 30
        or not args.clocks
        or any(c not in (396, 492) for c in args.clocks)
    ):
        parser.error("Invalid capture size, rounds or frame rate")
    try:
        data, frames = load_capture(args.capture)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    try:
        with args.output.open("w") as file:
            for round_ in range(args.rounds):
                for clock in args.clocks if round_ % 2 == 0 else reversed(args.clocks):
                    print(f"Replaying captured bytes: round {round_ + 1}, {clock} MHz", flush=True)
                    result = replay(data, frames, clock, args.fps, stop, repeats=args.repeat)
                    result["round"] = round_
                    file.write(json.dumps(result) + "\n")
                    file.flush()
                    print(
                        json.dumps(
                            {
                                k: result.get(k)
                                for k in [
                                    "clock",
                                    "completed",
                                    "expected",
                                    "failure",
                                    "submitted_frame",
                                ]
                            }
                        ),
                        flush=True,
                    )
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
