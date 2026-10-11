from pathlib import Path
import sys
import argparse
import threading
import contextlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from host import SlowReceiver
from wire import HEADER, ACK, CAPS, STREAM, CAP_BULK, CAP_STREAM, OK
from mirror import stream


class Receiver(SlowReceiver):
    def __init__(self, *args):
        super().__init__(*args)
        self.opens = 0
        self.streaming = False

    @contextlib.contextmanager
    def connection(self):
        self.opens += 1
        yield self

    def exchange(self, data):
        kind = data[5]
        session = HEADER.unpack_from(data)[5]
        if kind == CAPS:
            return ACK.pack(
                b"NDA1",
                1,
                OK,
                self.credit,
                self.need_key,
                session,
                self.accepted,
                self.displayed,
                1440,
                CAP_BULK | CAP_STREAM,
                0,
            )
        if kind == STREAM:
            self.streaming = True
            return ACK.pack(
                b"NDA1",
                1,
                OK,
                self.credit,
                self.need_key,
                session,
                self.accepted,
                self.displayed,
                1440,
                0,
                0,
            )
        return super().exchange(data)

    def batch(self, packets):
        assert self.streaming
        for packet in packets:
            reply = self.exchange(packet)
        return reply


stop = threading.Event()
receivers = []
messages = []


def factory(*args):
    receiver = Receiver(*args)
    receivers.append(receiver)
    return receiver


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
stream(args, stop, messages.append, bridge_factory=factory)
assert len(receivers) == 1 and receivers[0].accepted == 24 and receivers[0].closed
assert receivers[0].opens == 3, receivers[
    0
].opens  # Hello, caps, then one channel for the stream/polls
assert not any("Retrying" in message for message in messages), messages
print(
    "PASS persistent sender: one stream channel, ordered batches, same-channel credit polls, cancellation and cleanup"
)
