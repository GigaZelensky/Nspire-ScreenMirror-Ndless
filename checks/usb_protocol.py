"""Validate the direct transport against captured wire packets and adverse ordering."""

from pathlib import Path
import struct
import sys
import time
import ctypes as c
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from nspire_usb import (
    INNER,
    OUTER,
    NspireProtocol,
    WindowsUSB,
    data_checksum,
    internet_checksum,
    outer_packet,
    unpad_outer_transfer,
    packet,
    unpack_packet,
)
from wire import ACK, EVENT, HELLO, PAUSE, CAPS, CAP_PAUSE_PEER_CLOSE, control, acknowledgement

# A working TI connection's HELLO and its receiver response. Session data is
# synthetic; the packet vectors were cross-checked with libnspire's C checksums.
request = bytes.fromhex(
    "54fd64008000c8014008e3ee200000374e444d3101010000369b4f050000000000000000000000000000000000000000"
)
response = bytes.fromhex(
    "54fdc801400864008000c4522000007c4e44413101000201369b4f050000000000000000a00500000000000000000000"
)
assert packet(control(HELLO, 0x054F9B36)) == request
assert unpack_packet(response)[-1] == response[16:]
assert acknowledgement(response[16:], 0x054F9B36)["limit"] == 1440
assert data_checksum(bytes(range(255))) == 0x3922
assert outer_packet(1, b"\x01") == bytes.fromhex("0001fe010000000d000000f001")
assert outer_packet(0x84, sequence=4, source=1, destination=0xFE) == bytes.fromhex(
    "008401fe0000000c0004fd6d"
)
envelope = outer_packet(4, response, source=1, destination=0xFE)
for size in (12, 60, 192, 1472):
    message = outer_packet(4, bytes(size - 12), source=1, destination=0xFE)
    assert unpad_outer_transfer(message + b"\0") == message
    assert unpad_outer_transfer(message) == message
    assert unpad_outer_transfer(message[:-1]) == message[:-1]
    assert unpad_outer_transfer(message + b"\1") == (message if size % 64 == 0 else message + b"\1")
    assert unpad_outer_transfer(message + envelope) == message + envelope
    corrupted = bytearray(message + b"\0")
    corrupted[10] ^= 1
    assert unpad_outer_transfer(corrupted) == corrupted
for size in (0, 1, 254, 255, 1440):
    data = bytes(index & 255 for index in range(size))
    assert unpack_packet(packet(data))[-1] == data
for index in (0, 1, 10, 15, 16, len(request) - 1):
    bad = bytearray(request)
    bad[index] ^= 1
    try:
        unpack_packet(bad)
        raise AssertionError("Corrupt packet accepted")
    except OSError:
        pass
for bad in (request[:15], packet(b"x" * 255)[:18], request + b"extra"):
    try:
        unpack_packet(bad)
        raise AssertionError("Truncated/oversized packet accepted")
    except OSError:
        pass


def reply_packet(payload, source=0x4008, destination=0x8000):
    body = struct.pack(">I", len(payload)) + payload if len(payload) >= 255 else payload
    header = INNER.pack(
        0x54FD,
        0xC801,
        source,
        0x6400,
        destination,
        data_checksum(body),
        min(255, len(payload)),
        0,
        0,
        0,
    )
    return header[:15] + bytes([sum(header[:15]) & 255]) + body


class Device:
    def __init__(self, early_reply):
        self.pending = bytearray()
        self.writes = []
        self.early_reply = early_reply
        self.restarts = 0
        self.reply_extension = b""
        self.capabilities = 0

    def read(self, maximum, timeout):
        assert timeout > 0
        if not self.pending:
            raise TimeoutError("Empty simulated endpoint")
        # Split both header and payload across transfers; multiple messages
        # share the same pending stream, independent of USB transfer boundaries.
        data = bytes(self.pending[: min(7, maximum)])
        del self.pending[: len(data)]
        return data

    def write(self, data):
        self.writes.append(data)
        assert internet_checksum(data) == 0
        _, service, _, _, _, _, _, sequence, _ = OUTER.unpack_from(data)
        payload = data[12:]
        if service == 1 and payload == b"\x01":
            self.restarts += 1
            self.pending.extend(outer_packet(1, bytes(65), 101, source=1, destination=0xFF))
        elif service == 1 and payload == b"\x80":
            # The duplicate address response must not duplicate initialization.
            if len([p for p in self.writes if p[1] == 1 and p[12:] == b"\x80"]) % 2:
                self.pending.extend(
                    outer_packet(8, b"\x01", 102, source=1, destination=0xFE, req_ack=1)
                )
        elif service == 8:
            self.pending.extend(outer_packet(2, b"\0", 103, source=1, destination=0xFE))
        elif service == 4:
            source, destination, _, _, content = unpack_packet(payload)
            transport_ack = outer_packet(0x84, sequence=sequence, source=1, destination=0xFE)
            if source == 0x40DE:
                self.pending.extend(transport_ack)
                return
            session = struct.unpack_from("<I", content, 8)[0]
            features = self.capabilities if content[5] == CAPS else 0
            reply = ACK.pack(b"NDA1", 1, 0, 2, 1, session, 0, 0, 1440, features, 0)
            reply += self.reply_extension
            message = outer_packet(
                4,
                reply_packet(reply, destination=source),
                104,
                source=1,
                destination=0xFE,
                req_ack=1,
            )
            self.pending.extend(
                message + transport_ack if self.early_reply else transport_ack + message
            )


for early in (False, True):
    device = Device(early)
    protocol = NspireProtocol(device)
    for session in range(1, 5):
        assert protocol.begin()
        assert protocol.ready
        assert acknowledgement(protocol.exchange(control(HELLO, session)), session)["status"] == 0
        assert acknowledgement(protocol.exchange(control(PAUSE, session)), session)["status"] == 0
        protocol.end()
        # Reopen like the USB helper's normal per-control connections.
        protocol = NspireProtocol(device)
    assert device.restarts == 4
    assert not any(p[1] == 4 and unpack_packet(p[12:])[0] == 0x40DE for p in device.writes), (
        "PAUSE callback completion was followed by a racing ECLOSE"
    )

ports = [NspireProtocol(Device(False)).host for _ in range(100)]
assert len(set(ports)) == len(ports) and all(0x8000 <= port < 0xFFFF for port in ports)
# Logical channels share their physical link and NNSE sequence. Reinitializing
# address/time between CAPS, CLOCK, INPUT and STREAM races native link cleanup.
device = Device(True)
protocol = NspireProtocol(device)
for session in range(1, 17):
    assert protocol.begin()
    protocol.exchange(control(HELLO, session))
    protocol.exchange(control(PAUSE, session))
    protocol.end()
assert device.restarts == 1
# Match the production helper's begin/end API: logical end keeps the same I/O
# handle, and helper close releases it once. This also exercises shared NNSE state.
device = Device(True)
opened, closed = [], []
device.close = lambda: closed.append(True)
fake_winusb = SimpleNamespace(
    paths=lambda: ["fake_pid_e022"],
    WinUSB=lambda path: (opened.append(path), device)[1],
)
with patch.dict(sys.modules, winusb=fake_winusb):
    usb = WindowsUSB()
    for session in range(1, 17):
        assert usb.begin()
        usb.exchange(control(HELLO, session))
        usb.exchange(control(PAUSE, session))
        usb.end()
        assert usb.protocol and not usb.protocol.connected and not closed
    usb.close()
    usb.close()
assert len(opened) == device.restarts == len(closed) == 1 and usb.protocol is None
# Control channels that have not returned PAUSE still need explicit disconnect.
device = Device(False)
protocol = NspireProtocol(device)
assert protocol.begin()
protocol.exchange(control(HELLO, 100))
protocol.end()
assert any(p[1] == 4 and unpack_packet(p[12:])[0] == 0x40DE for p in device.writes)


# A complete, checksummed padded transfer must not shift the next envelope.
for early in (False, True):
    device = Device(early)
    device.capabilities = CAP_PAUSE_PEER_CLOSE
    protocol = NspireProtocol(device)
    for session in range(1, 9):
        assert protocol.begin()
        protocol.exchange(control(HELLO, session))
        protocol.exchange(control(CAPS, session))
        assert protocol.pause_peer_close
        protocol.exchange(control(PAUSE, session))
        assert not protocol.peer_finished
        before = len(device.writes)
        protocol.end()
        assert (
            sum(p[1] == 4 and unpack_packet(p[12:])[0] == 0x40DE for p in device.writes[before:])
            == 1
        )
        protocol.end()
    assert device.restarts == 1


class TransferDevice(Device):
    def __init__(self):
        super().__init__(True)
        self.transfers = [envelope + b"\0", envelope]

    def read(self, maximum, timeout):
        return self.transfers.pop(0)


device = TransferDevice()
protocol = NspireProtocol(device)
protocol.receive_outer(time.monotonic() + 1)
protocol.receive_outer(time.monotonic() + 1)
assert list(protocol.incoming) == [response, response] and not protocol.buffer

# CX II pads aligned messages with an unspecified byte. A nonzero trailer
# previously remained buffered and made the next ACK look like length zero.
input_reply = response[16:] + b"NDE1" + struct.pack("<I", 5)
input_reply += b"".join(EVENT.pack(i, 1, 0, 0, i, 0) for i in range(1, 6))
aligned = outer_packet(4, reply_packet(input_reply), source=1, destination=0xFE)
assert len(aligned) == 128  # A normal reply carrying five touchpad events.
ack = outer_packet(0x84, sequence=0x02FA, source=1, destination=0xFE)
assert ack.hex().startswith("008401fe0000000c02fafa")
for trailer in range(256):
    assert unpad_outer_transfer(aligned + bytes([trailer])) == aligned
    device = TransferDevice()
    device.transfers = [aligned + bytes([trailer]), ack]
    protocol = NspireProtocol(device)
    protocol.host = 0x8000  # Captured fixture's destination.
    protocol.receive_outer(time.monotonic() + 1)
    assert protocol.receive_outer(time.monotonic() + 1) == (0x84, 0x02FA)
    delivered = protocol.handle_inner(protocol.incoming.popleft())
    assert len(acknowledgement(delivered, 0x054F9B36)["events"]) == 5
    assert not protocol.buffer
for malformed in (
    aligned[:-1] + b"\xa0",
    aligned + b"\xa0\xa0",
    aligned + ack,
    aligned[:11] + b"\xa0",
):
    assert unpad_outer_transfer(malformed) == malformed
bad = bytearray(aligned + b"\xa0")
bad[13] ^= 1
assert unpad_outer_transfer(bad) == bad

# Reproduce the reported failure shape by leaving the old trailer in the
# stream. An already malformed stream still fails instead of scanning ahead.
device = TransferDevice()
device.transfers = [ack]
protocol = NspireProtocol(device)
protocol.buffer.extend(b"\xa0")
try:
    protocol.receive_outer(time.monotonic() + 1)
    raise AssertionError("Stray leading byte was silently discarded")
except OSError as error:
    assert "(0; buffered=13; header=a0008401fe0000000c02fafa)" in str(error)

# A leading fragment of the next message cannot be silently removed as padding.
device = TransferDevice()
device.transfers = [envelope[:5], envelope[5:] + envelope[:1], envelope[1:]]
protocol = NspireProtocol(device)
protocol.receive_outer(time.monotonic() + 1)
protocol.receive_outer(time.monotonic() + 1)
assert list(protocol.incoming) == [response, response] and not protocol.buffer

for data in (outer_packet(4, b"x")[:-1], b"\0" * 12):
    device = Device(False)
    device.pending.extend(data)
    protocol = NspireProtocol(device)
    try:
        protocol.receive_outer(time.monotonic() + 0.1)
        raise AssertionError("Incomplete/malformed envelope accepted")
    except OSError:
        pass

# Large replies test the production size bound and extended inner header.
device = Device(True)
protocol = NspireProtocol(device)
assert protocol.begin()
words = [0] * 80
words[48], words[78] = 1, 5504
device.reply_extension = b"NDS1" + struct.pack("<I80I", 80, *words)
large = protocol.exchange(control(HELLO, 73))
assert (
    len(large) == 360
    and acknowledgement(large, 73)["profile"]["sram_tables"]["table_bytes"] == 5504
)
device.reply_extension += b"x"
try:
    protocol.exchange(control(HELLO, 74))
except OSError:
    pass
else:
    raise AssertionError("Oversized diagnostic reply accepted")
protocol.end()

# Exercise the production WinUSB initialization against a fake Windows API.
# This catches omission of transfer termination, which protocol-only mocks do
# not model: exact endpoint-size writes otherwise hang on physical hardware.
from winusb import WinUSB, Descriptor, Pipe

policies, released = [], []


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class Library:
    def __init__(self, functions):
        for name, function in functions.items():
            setattr(self, name, Function(function))


def initialize(file, pointer):
    c.cast(pointer, c.POINTER(c.c_void_p))[0] = c.c_void_p(123)
    return 1


def query(handle, alternate, pointer):
    c.cast(pointer, c.POINTER(Descriptor))[0].endpoints = 2
    return 1


def pipe(handle, alternate, index, pointer):
    info = c.cast(pointer, c.POINTER(Pipe))[0]
    info.type, info.id, info.maximum = 2, (0x81 if index == 0 else 1), 512
    return 1


def policy(handle, endpoint, kind, length, pointer):
    policies.append((endpoint, kind, length, c.string_at(pointer, length)))
    return 1


kernel = Library(
    {"CreateFileW": lambda *args: 42, "CloseHandle": lambda handle: released.append("file") or 1}
)
usb = Library(
    {
        "WinUsb_Initialize": initialize,
        "WinUsb_Free": lambda handle: released.append("usb") or 1,
        "WinUsb_QueryInterfaceSettings": query,
        "WinUsb_QueryPipe": pipe,
        "WinUsb_SetPipePolicy": policy,
        "WinUsb_ReadPipe": lambda *args: 1,
        "WinUsb_WritePipe": lambda *args: 1,
    }
)
with patch.object(
    c,
    "WinDLL",
    side_effect=lambda name, **kwargs: kernel if name == "kernel32" else usb,
    create=True,
):
    connection = WinUSB("synthetic interface")
    connection.close()
    connection.close()
assert (1, 1, 1, b"\x01") in policies
assert released == ["usb", "file"]
assert not any(endpoint == 0x81 and kind == 1 for endpoint, kind, _, _ in policies)

print(
    "PASS captured USB vectors, extended packets, corruption/length rejection, split/coalesced transfers, early replies and repeated handshakes"
)
