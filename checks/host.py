"""Sender flow against a deliberately slow calculator; no USB device required."""

from pathlib import Path
import argparse
import contextlib
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from wire import HEADER, ACK, HELLO, POLL, DATA, CAPS, OK, BAD, LAST
from mirror import stream
import mirror

# This simulated transport does not compete with real TI software.
mirror.linking_apps = lambda: []


class SlowReceiver:
    def __init__(self, stop, *unused):
        self.stop = stop
        self.session = 0
        self.accepted = 0
        self.displayed = 0
        self.used = 0
        self.queued = []
        self.polls = 0
        self.credit = 2
        self.closed = False
        self.need_key = 1

    @contextlib.contextmanager
    def connection(self):
        yield self

    def exchange(self, data):
        (
            magic,
            version,
            kind,
            flags,
            reserved,
            session,
            sequence,
            total,
            offset,
            size,
            zero,
            zero2,
        ) = HEADER.unpack_from(data)
        assert magic == b"NDM1" and version == 1 and reserved == zero == zero2 == 0
        assert len(data) == 32 + size
        if kind == CAPS:
            return ACK.pack(
                b"NDA1",
                1,
                BAD,
                self.credit,
                self.need_key,
                session,
                self.accepted,
                self.displayed,
                1440,
                self.accepted,
                0,
            )
        if kind == HELLO:
            self.session = session
        elif kind == POLL:
            self.polls += 1
            if self.polls % 3 == 0 and self.queued:
                self.displayed = self.queued.pop(0)
                self.credit += 1
        else:
            assert kind == DATA and self.credit > 0
            if offset == 0:
                assert sequence == self.accepted + 1
                self.used = 0
            assert sequence == self.accepted + 1 and self.used == offset
            self.used += size
            if flags & LAST:
                assert self.used == total
                self.accepted = sequence
                self.credit -= 1
                self.queued.append(sequence)
                self.need_key = 0
                assert len(self.queued) <= 2
                if self.accepted == 24:
                    self.stop.set()
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
            self.accepted,
            0,
        )

    def close(self):
        self.closed = True


def main():
    stop = threading.Event()
    receivers = []
    messages = []

    def factory(*args):
        receiver = SlowReceiver(*args)
        receivers.append(receiver)
        return receiver

    args = argparse.Namespace(
        test_pattern=True,
        fps=30,
        crf=20,
        backend=None,
        library=None,
        packet_size=None,
        region=None,
        monitor=1,
    )
    stream(args, stop, messages.append, bridge_factory=factory)
    assert (
        len(receivers) == 1
        and receivers[0].closed
        and receivers[0].accepted == 24
        and receivers[0].polls > 0
    )
    assert not any("Retrying" in m for m in messages), messages
    print(
        "PASS sender backpressure, ordered fragmentation, bounded two-frame queue, cancellation and helper cleanup"
    )


if __name__ == "__main__":
    main()
