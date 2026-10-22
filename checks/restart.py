"""Closing replies, Stop RPC ordering and GUI-owned desktop restart lifecycle."""

from pathlib import Path
import argparse
import threading
import sys
import tkinter as tk
import contextlib
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from host import SlowReceiver
from wire import ACK, HEADER, OK, CLOSING, CAPS, STREAM, CAP_BULK, CAP_STREAM, CAP_PAUSE, PAUSE
from transport import Bridge
import mirror
import virtual_display


class Receiver(SlowReceiver):
    def __init__(self, *args):
        super().__init__(*args)
        self.paused = 0

    def exchange(self, data):
        kind = data[5]
        session = HEADER.unpack_from(data)[5]
        if kind == CAPS:
            return ACK.pack(
                b"NDA1", 1, OK, 2, 1, session, 0, 0, 1440, CAP_BULK | CAP_STREAM | CAP_PAUSE, 0
            )
        if kind == STREAM:
            return ACK.pack(b"NDA1", 1, OK, 2, 1, session, 0, 0, 1440, 0, 0)
        return super().exchange(data)

    def pause_session(self, session, input_ack=0):
        assert session == self.session
        self.paused += 1


class ClosingReceiver(Receiver):
    def batch(self, packets):
        return ACK.pack(b"NDA1", 1, CLOSING, 2, 1, self.session, 0, 0, 1440, 0, 0)


class PausingReceiver(Receiver):
    def batch(self, packets):
        for packet in packets:
            reply = self.exchange(packet)
        if self.accepted == 2:
            self.stop.set()
        return reply


args = argparse.Namespace(
    test_pattern=True,
    fps=60,
    crf=14,
    backend=None,
    library=None,
    packet_size=None,
    region=None,
    monitor=1,
    persistent=True,
)
for kind in (ClosingReceiver, PausingReceiver):
    receivers = []
    messages = []
    stop = threading.Event()

    def factory(*values):
        r = kind(*values)
        receivers.append(r)
        return r

    mirror.stream(args, stop, messages.append, bridge_factory=factory)
    assert len(receivers) == 1 and receivers[0].closed
    assert not any("Retrying" in message for message in messages), messages
    assert receivers[0].paused == (kind is PausingReceiver)
    if kind is ClosingReceiver:
        assert "receiver closed" in messages[-1]


class FailedStreamOpen(Receiver):
    def __init__(self, *values):
        super().__init__(*values)
        self.connections = 0

    @contextlib.contextmanager
    def connection(self):
        self.connections += 1
        if self.connections == 3:
            self.stop.set()
            raise OSError("simulated stream open failure")
        yield


receivers, messages = [], []
stop = threading.Event()


def failed_factory(*values):
    receiver = FailedStreamOpen(*values)
    receivers.append(receiver)
    return receiver


mirror.stream(args, stop, messages.append, bridge_factory=failed_factory)
assert len(receivers) == 1 and receivers[0].closed and receivers[0].paused == 0
assert receivers[0].connections == 3
assert not any("cleanup:" in message for message in messages), messages


# Reproduce a frame timeout followed by a failing ECLOSE. Cleanup must not
# terminate the outer retry loop or misreport a desktop-capture failure.
class FailedChannelClose(Receiver):
    @contextlib.contextmanager
    def connection(self):
        yield self
        if self.accepted:
            raise OSError("simulated native channel-close timeout")

    def batch(self, packets):
        for packet in packets:
            self.exchange(packet)
        raise OSError("simulated frame timeout")


receivers, messages = [], []
stop = threading.Event()


def cleanup_failure_factory(*values):
    receiver = (FailedChannelClose if not receivers else ClosingReceiver)(*values)
    receivers.append(receiver)
    return receiver


mirror.stream(args, stop, messages.append, bridge_factory=cleanup_failure_factory)
assert len(receivers) == 2 and all(receiver.closed for receiver in receivers)
assert any("USB channel cleanup:" in message for message in messages), messages
assert "receiver closed" in messages[-1]
assert not any("Capture could not start" in message for message in messages), messages


# A cancelled frame reply must be drained, not mistaken for the PAUSE reply.
class Pipe:
    def __init__(self):
        self.results = [
            ("ok", b"old frame reply"),
            ("ok", ACK.pack(b"NDA1", 1, OK, 2, 1, 73, 0, 0, 1440, 0, 0)),
        ]
        self.sent = []

    def poll(self, timeout):
        return bool(self.results)

    def recv(self):
        return self.results.pop(0)

    def send(self, packet):
        self.sent.append(packet)


bridge = Bridge.__new__(Bridge)
bridge.pending = True
bridge.stop = threading.Event()
bridge.stop.set()
bridge.pipe = Pipe()
bridge.pause_session(73)
assert not bridge.pending and not bridge.pipe.results
assert len(bridge.pipe.sent) == 1 and bridge.pipe.sent[0][1][5] == PAUSE

# A helper error already consumed its RPC slot. Stop must send PAUSE directly,
# not wait for another frame reply that can never arrive.
bridge = Bridge.__new__(Bridge)
bridge.pending = False
bridge.stop = threading.Event()
bridge.pipe = Pipe()
bridge.pipe.results[0] = ("error", "simulated frame transport failure")
try:
    bridge.command("exchange", b"frame")
    raise AssertionError("Helper error swallowed")
except OSError:
    pass
assert not bridge.pending
bridge.stop.set()
bridge.pause_session(73)
assert not bridge.pending and not bridge.pipe.results
assert [kind for kind, _ in bridge.pipe.sent] == ["exchange", "exchange"]

# Exercise the real GUI callbacks without USB or real monitor mutations.
leases = []
runs = []
input_options = []
clock_options = []


class Lease:
    def __init__(self, size):
        self.size = size
        self.name = None
        self.opens = 0
        self.closes = 0
        leases.append(self)

    def open(self):
        assert threading.current_thread() is threading.main_thread()
        self.name = "owned"
        self.opens += 1
        self.region = dict(left=2560, top=0, width=self.size[0], height=self.size[1])
        return self

    def close(self):
        assert threading.current_thread() is threading.main_thread()
        self.name = None
        self.closes += 1


def stream(options, stop, report, **unused):
    assert options.connection_log is None
    runs.append(options.display_lease)
    input_options.append((options.reverse_scroll, options.scroll_gain, options.pointer_gain))
    clock_options.append(options.clock)
    assert options.display_lease.name == "owned"
    stop.wait(3)


virtual_display.VirtualDisplay = Lease
virtual_display.resolution_available = lambda size: True
virtual_display.installed = lambda: True
mirror.stream = stream
OriginalTk = tk.Tk


class Window(OriginalTk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.phase = 0
        self.deadline = time.monotonic() + 10
        self.after(20, self.advance)

    def widgets(self, parent=None):
        for child in (parent or self).winfo_children():
            yield child
            yield from self.widgets(child)

    def button(self, label):
        return next(
            widget
            for widget in self.widgets()
            if widget.winfo_class() == "TButton" and widget.cget("text") == label
        )

    def advance(self):
        if time.monotonic() > self.deadline:
            self.destroy()
            return
        start = self.button("Start mirroring")
        clock = next(
            widget
            for widget in self.widgets()
            if widget.winfo_class() == "TCombobox" and "480 MHz" in widget.cget("values")
        )
        if self.phase == 0 and runs:
            assert str(clock.cget("state")) == "disabled"
            self.button("Stop").invoke()
            self.phase = 1
        elif self.phase == 1 and "disabled" not in start.state():
            assert len(leases) == 1 and leases[0].closes == 0
            assert str(clock.cget("state")) == "normal"
            clock.set("400")
            start.invoke()
            assert len(runs) == 1 and leases[0].opens == 1
            clock.set("480")
            start.invoke()
            self.phase = 2
        elif self.phase == 2 and len(runs) == 2:
            self.button("Stop").invoke()
            self.phase = 3
        elif self.phase == 3 and "disabled" not in start.state():
            assert str(clock.cget("state")) == "normal"
            self.tk.call(self.protocol("WM_DELETE_WINDOW"))
            return
        self.after(20, self.advance)


tk.Tk = Window
mirror.gui(
    argparse.Namespace(
        monitor=1,
        fps=30,
        crf=14,
        test_pattern=False,
        virtual_display=True,
        desktop_size=(640, 480),
        input=False,
        reverse_scroll=True,
        scroll_gain=0.5,
        pointer_gain=1.5,
        start=True,
        region=None,
    )
)
assert len(leases) == 1 and len(runs) == 2 and runs[0] is runs[1]
assert leases[0].opens == 2 and leases[0].closes == 1
assert input_options == [(True, 0.5, 1.5), (True, 0.5, 1.5)]
assert clock_options == [0, 480]
print(
    "PASS closing reply stops retries; Stop drains pending RPC and pauses; GUI retains one desktop across Stop/Start and releases it on Close"
)
