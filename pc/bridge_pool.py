"""Keep a healthy direct Windows USB link across desktop Stop/Start sessions."""

import contextlib
import os
import threading
from transport import Bridge


class BridgePool:
    def __init__(self, factory=Bridge):
        self.factory = factory
        self.idle = None
        self.active = None
        self.closed = False
        self.condition = threading.Condition()

    def __call__(self, stop, backend=None, path=None):
        selected = backend or ("windows" if os.name == "nt" else "linux")
        if selected != "windows" or path is not None:
            return self.factory(stop, backend, path)
        with self.condition:
            if self.closed or self.active is not None:
                raise OSError("USB connection is already in use or closed")
            bridge, self.idle = self.idle, None
            if bridge and (bridge.pending or not bridge.process.is_alive()):
                bridge.close()
                bridge = None
            if bridge is None:
                bridge = self.factory(stop, backend, path)
            bridge.stop = stop
            lease = _Lease(self, bridge)
            self.active = lease
            return lease

    def release(self, lease):
        bridge = lease.bridge
        reusable = False
        try:
            if lease.healthy and lease.paused and not bridge.pending and bridge.process.is_alive():
                # Stop is set. Consume the end reply without treating cancellation
                # as permission to skip the native channel close.
                bridge.pipe.send(("end", None))
                bridge.pending = True
                bridge.receive(2, ignore_stop=True)
                reusable = True
        except (OSError, EOFError):
            # Never cache a link whose native channel retirement is uncertain.
            reusable = False
        with self.condition:
            retain = reusable and not self.closed
            if retain:
                self.idle = bridge
        try:
            if not retain:
                bridge.close()
        finally:
            with self.condition:
                self.active = None
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            if self.active:
                self.active.bridge.stop.set()
                # Normal GUI shutdown joins the stream first. Also bound cleanup
                # after an exceptional mainloop exit without sharing its RPC pipe.
                finished = self.condition.wait_for(lambda: self.active is None, timeout=6)
                if not finished and self.active.bridge.process.is_alive():
                    self.active.bridge.process.terminate()
                    self.active.bridge.process.join(2)
            bridge, self.idle = self.idle, None
        if bridge:
            bridge.close()


class _Lease:
    def __init__(self, pool, bridge):
        self.pool, self.bridge = pool, bridge
        self.paused = False
        self.healthy = True
        self.released = False

    def __getattr__(self, name):
        return getattr(self.bridge, name)

    @contextlib.contextmanager
    def connection(self):
        if self.released:
            raise OSError("USB session has already ended")
        try:
            with self.bridge.connection():
                yield self
        except InterruptedError:
            raise
        except (OSError, EOFError):
            self.healthy = False
            raise

    def _call(self, name, *args):
        if self.released:
            raise OSError("USB session has already ended")
        try:
            return getattr(self.bridge, name)(*args)
        except InterruptedError:
            raise
        except (OSError, EOFError):
            self.healthy = False
            raise

    def exchange(self, data):
        return self._call("exchange", data)

    def batch(self, packets):
        return self._call("batch", packets)

    def pause_session(self, session, input_ack=0):
        result = self._call("pause_session", session, input_ack)
        self.paused = True
        return result

    def close(self):
        if not self.released:
            self.released = True
            self.pool.release(self)
