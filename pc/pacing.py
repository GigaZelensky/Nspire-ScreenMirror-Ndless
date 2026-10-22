"""Absolute capture cadence with a high-resolution, monotonic frame clock."""

import time


def wait_for_deadline(stop, deadline, tick=None):
    """Python's high-resolution sleep, with bounded stop/input latency.

    Event.wait uses a Windows synchronization timeout, which can round a
    sub-frame wait to a scheduler tick. Python 3.11+ sleep uses a high-resolution
    waitable timer there; no busy spin or permanent worker thread is needed.
    Use perf_counter for deadlines too: Python 3.12's Windows monotonic clock
    is GetTickCount64 with 15.6 ms resolution, despite the precise sleep.
    """
    while not stop.is_set():
        delay = deadline - time.perf_counter()
        if delay <= 0:
            return True
        time.sleep(min(delay, 0.008))
        if tick:
            tick()
    return False


def next_deadline(previous, completed, fps):
    period = 1 / fps
    deadline = previous + period
    return completed if deadline < completed - period else deadline
