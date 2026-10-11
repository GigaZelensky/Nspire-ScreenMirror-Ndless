"""Check clean pause reuse, failed retirement, cancellation and GUI shutdown."""

from pathlib import Path
import contextlib
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from bridge_pool import BridgePool


class FakeBridge:
    def __init__(self, stop, backend, path):
        self.stop = stop
        self.pending = False
        self.alive = True
        self.closed = 0
        self.calls = []
        self.fail_end = False
        self.fail_exchange = False
        self.interrupt_batch = False
        self.process = SimpleNamespace(is_alive=lambda: self.alive)
        self.pipe = SimpleNamespace(send=lambda value: self.calls.append(value))

    @contextlib.contextmanager
    def connection(self):
        yield self

    def exchange(self, data):
        if self.fail_exchange:
            raise OSError("transfer failed")
        return data

    def batch(self, data):
        if self.interrupt_batch:
            self.pending = True
            raise InterruptedError("Stop during transfer")
        return data

    def pause_session(self, session, input_ack):
        self.pending = False  # production method drains the interrupted reply
        self.calls.append(("pause", session))

    def receive(self, timeout, ignore_stop=False):
        assert ignore_stop and self.calls[-1] == ("end", None)
        if self.fail_end:
            raise TimeoutError("retirement failed")
        self.pending = False

    def close(self):
        self.closed += 1
        self.alive = False


created = []


def factory(*args):
    bridge = FakeBridge(*args)
    created.append(bridge)
    return bridge


pool = BridgePool(factory)
first_stop = threading.Event()
first = pool(first_stop, "windows", None)
base = first.bridge
first_stop.set()
first.pause_session(1)
first.close()
first.close()
assert base.closed == 0 and base.calls == [("pause", 1), ("end", None)]
second_stop = threading.Event()
second = pool(second_stop, "windows", None)
assert second.bridge is base and base.stop is second_stop
try:
    first.exchange(b"late")
    raise AssertionError("released lease remained usable")
except OSError:
    pass
try:
    pool(threading.Event(), "windows", None)
    raise AssertionError("parallel lease accepted")
except OSError:
    pass
# Cancellation can still yield a healthy link after a drained reply + PAUSE.
base.interrupt_batch = True
try:
    second.batch([b"frame"])
except InterruptedError:
    pass
second_stop.set()
second.pause_session(2)
second.close()
assert pool.idle is base and not base.pending
pool.close()
pool.close()
assert base.closed == 1
try:
    pool(threading.Event(), "windows", None)
    raise AssertionError("closed pool accepted work")
except OSError:
    pass

for fault in ("no_pause", "end_error", "exchange_error", "helper_dead", "pending"):
    pool = BridgePool(factory)
    lease = pool(threading.Event(), "windows", None)
    bridge = lease.bridge
    if fault != "no_pause":
        lease.pause_session(3)
    if fault == "end_error":
        bridge.fail_end = True
    elif fault == "exchange_error":
        bridge.fail_exchange = True
        try:
            lease.exchange(b"frame")
        except OSError:
            pass
    elif fault == "helper_dead":
        bridge.alive = False
    elif fault == "pending":
        bridge.pending = True
    lease.close()
    assert pool.idle is None and bridge.closed == 1, fault
    pool.close()

# Other transports keep their existing lifetime and cleanup behavior.
pool = BridgePool(factory)
legacy = pool(threading.Event(), "navnet", "legacy")
assert isinstance(legacy, FakeBridge) and pool.active is None
legacy.close()
pool.close()
print(
    "PASS clean Stop reuse, fresh cancellation token, failed-link discard, shutdown and backend isolation"
)
