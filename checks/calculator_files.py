"""Check read-only file retrieval, bounds, service isolation and archive naming."""

from pathlib import Path
import struct
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from calculator_files import (
    FileClient,
    directory_entry,
    wire_string,
    save_download,
    file_usb_message,
)
from nspire_usb import packet, unpack_packet, outer_packet


class Device:
    def __init__(self, data=b"saved diagnostic\n", header=None, chunks=None):
        self.host = 0x8000
        self.cx2 = True
        self.data = data
        self.header = header
        self.chunks = chunks
        self.sent = []
        self.pending = []

    def send(self, data, source=None, destination=None):
        assert destination == 0x4060
        self.sent.append((self.host, source, data))
        if data.startswith(b"\x07\x01"):
            self.pending.append(
                self.header
                if self.header is not None
                else b"\x03\x01" + bytes(9) + struct.pack(">I", len(self.data))
            )
        elif data == b"\x04":
            self.pending.extend(
                self.chunks
                if self.chunks is not None
                else [b"\x05" + self.data[:7], b"\x05" + self.data[7:]]
            )

    def receive(self, deadline):
        return packet(self.pending.pop(0), source=0x4060, destination=self.host)

    def handle_inner(self, message):
        return unpack_packet(message)[-1]


device = Device()
client = FileClient(device)
assert client.download("/ndless/ndmirror.log.tns") == device.data
assert device.sent[0][2] == b"\x07\x01/ndless/ndmirror.log.tns\0"
assert device.sent[-2][2] == b"\xff\0"
assert device.sent[-1] == (0x8000, 0x40DE, b"\x80\0")
assert device.host == 0x8001
assert wire_string("/") == b"/" + bytes(8)
assert wire_string("123456789") == b"123456789\0"
message = outer_packet(4, packet(bytes(1440), source=0x4060))
assert file_usb_message(message + b"\0") == message
assert file_usb_message(message + b"\xa0") == message
for unchanged in (message, message[:512], message + message, message + b"xx"):
    assert file_usb_message(unchanged) == unchanged
broken = bytearray(message)
broken[11] ^= 1
assert file_usb_message(bytes(broken) + b"\0") == bytes(broken) + b"\0"
for invalid in ("", "a\0b", "x" * 240):
    try:
        wire_string(invalid)
        raise AssertionError("Invalid path accepted")
    except ValueError:
        pass

for name in ("ndless", "ndmirror.log.tns"):
    entry = b"\x10\0\x19" + wire_string(name) + struct.pack(">IIBB", 123, 456, 0, 0)
    result = directory_entry(entry)
    assert (result.name, result.size, result.modified, result.directory) == (name, 123, 456, False)
for invalid in (
    b"\x0e",
    b"\x0e\x01\0" + b"unterminated",
    b"\x0e\x01\0" + wire_string("../log") + struct.pack(">IIBB", 1, 0, 0, 0),
):
    try:
        directory_entry(invalid)
        raise AssertionError("Invalid directory entry accepted")
    except OSError:
        pass


class DirectoryDevice(Device):
    def __init__(self):
        super().__init__()
        self.enumerated = False

    def send(self, data, source=None, destination=None):
        super().send(data, source, destination)
        if data.startswith(b"\x0d"):
            assert data == b"\x0d/" + bytes(9)
            self.pending.append(b"\xff\0")
        elif data == b"\x0e":
            if self.enumerated:
                self.pending.append(b"\xff\x11")
            else:
                self.enumerated = True
                self.pending.append(
                    b"\x10\0\x19"
                    + wire_string("ndmirror.log.tns")
                    + struct.pack(">IIBB", 123, 456, 0, 0)
                )
        elif data == b"\x0f":
            self.pending.append(b"\xff\0")


device = DirectoryDevice()
entries = FileClient(device).list_directory("/")
assert len(entries) == 1 and entries[0].name == "ndmirror.log.tns"
assert device.sent[-1] == (0x8000, 0x40DE, b"\x80\0")


class UploadDevice(Device):
    def __init__(self, cx2=True, corrupt=False):
        super().__init__()
        self.cx2 = cx2
        self.corrupt = corrupt
        self.uploaded = bytearray()
        self.expected = None

    def send(self, data, source=None, destination=None):
        super().send(data, source, destination)
        if data.startswith(b"\x03\x01"):
            self.expected = struct.unpack_from(">I", data, len(data) - 4)[0]
            self.pending.append(b"\x04")
        elif self.expected is not None and data[:1] == b"\x05":
            assert 2 <= len(data) <= (1440 if self.cx2 else 254)
            self.uploaded.extend(data[1:])
            if len(self.uploaded) == self.expected:
                self.data = bytes(self.uploaded)
                if self.corrupt:
                    self.data = bytes([self.data[0] ^ 1]) + self.data[1:]
                self.chunks = [
                    b"\x05" + self.data[i : i + 253] for i in range(0, len(self.data), 253)
                ]
                self.pending.append(b"\xff\0")
                self.expected = None


for cx2 in (True, False):
    device = UploadDevice(cx2=cx2)
    payload = bytes(range(256)) * 13
    assert FileClient(device).upload("/ndmirror.tns", payload) == len(payload)
    assert device.uploaded == payload and device.host == 0x8002
device = UploadDevice(corrupt=True)
try:
    FileClient(device).upload("/ndmirror.tns", b"new receiver")
    raise AssertionError("Corrupt readback accepted")
except OSError as error:
    assert "read-back differs" in str(error)
device = UploadDevice()
for invalid in (b"", bytes(8 * 1024 * 1024 + 1)):
    try:
        FileClient(device).upload("/ndmirror.tns", invalid)
        raise AssertionError("Invalid upload size accepted")
    except ValueError:
        pass
assert not device.sent

for header, chunks in (
    (b"\xff\x0a", None),
    (b"\x03\x01" + bytes(9) + struct.pack(">I", 9 * 1024 * 1024), None),
    (b"\x03\x01" + bytes(8), None),
    (None, [b"\x05"]),
    (None, [b"\x05" + bytes(100)]),
    (None, [b"\x06bad"]),
):
    device = Device(header=header, chunks=chunks)
    try:
        FileClient(device).download("/ndmirror.log.tns")
        raise AssertionError("Invalid transfer accepted")
    except OSError:
        pass
    assert device.sent[-1] == (0x8000, 0x40DE, b"\x80\0")

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    existing = folder / "ndmirror.log.tns"
    existing.write_bytes(b"older evidence")
    device = Device()
    first = save_download(FileClient(device), "/ndmirror.log.tns", folder)
    second = save_download(FileClient(device), "/ndmirror.log.tns", folder)
    assert first != second and first.read_bytes() == second.read_bytes() == device.data
    assert existing.read_bytes() == b"older evidence"
    assert first.with_suffix(".tns.json").is_file()

print(
    "PASS file-service download/upload, malformed input, closure, readback and evidence preservation"
)
