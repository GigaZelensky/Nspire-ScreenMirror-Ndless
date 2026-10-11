"""Validate acknowledgement isolation and safe release for the hardware probe."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/benchmarks"))
from held_write import HoldAck
from nspire_usb import outer_packet


class IO:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    def read(self, *args):
        return b"received"


io = IO()
held = HoldAck(io)
ack = outer_packet(0x84, sequence=12)
held.write(ack)
assert held.withheld == [ack] and not io.sent
other = outer_packet(0x81, sequence=10)
request = outer_packet(4, b"frame")
held.write(other)
held.write(request)
assert io.sent == [other, request]
assert held.read(20, 10) == b"received"
held.release()
assert io.sent == [other, request, ack] and not held.withheld
held.release()
assert len(io.sent) == 3
held.write(ack)
assert io.sent[-1] == ack
held = HoldAck(IO())
for _ in range(64):
    held.write(ack)
try:
    held.write(ack)
    raise AssertionError("Unbounded acknowledgements")
except RuntimeError:
    pass
held.release()
assert len(held.io.sent) == 64
print("PASS held-write probe: exact ACK suppression, other packets, release, bounds")
