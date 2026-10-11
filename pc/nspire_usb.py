"""Nspire packet/session protocol, independent of TI's PC libraries.

Protocol reference: Vogtinator/libnspire, revision
7d7d962ca49ff9017f6f146336c51ef7f2edf4e0 (GPL-3.0-or-later).
See docs/usb-protocol.md for attribution and transport details.
"""

from collections import deque
import struct
import time
import itertools
import secrets

from wire import SERVICE, MAX_REPLY, PAUSE, OK, CAPS, CAP_PAUSE_PEER_CLOSE

_host_ports = itertools.count(secrets.randbelow(0x7FFF))

INNER = struct.Struct(">6H4B")
OUTER = struct.Struct(">6B3H")


def data_checksum(data):
    crc = 0
    for byte in data:
        low = crc & 255
        mixed = ((low & 15) << 4 ^ low) << 8
        shifted = mixed >> 5
        crc = (shifted >> 7) ^ (byte << 8 | crc >> 8) ^ mixed ^ shifted
    return crc


def internet_checksum(data):
    total = sum(struct.unpack(">" + "H" * (len(data) // 2), data[: len(data) & ~1]))
    if len(data) & 1:
        total += data[-1] << 8
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return total ^ 65535


def packet(data, source=0x8000, destination=SERVICE, sequence=0, ack=0, device_address=0xC801):
    if len(data) > 1440:
        raise ValueError("Nspire packet exceeds 1440 bytes")
    size = len(data) if len(data) < 255 else 255
    body = data if size != 255 else struct.pack(">I", len(data)) + data
    header = INNER.pack(
        0x54FD,
        0x6400,
        source,
        device_address,
        destination,
        data_checksum(body),
        size,
        ack,
        sequence,
        0,
    )
    return header[:-1] + bytes([sum(header[:-1]) & 255]) + body


def unpack_packet(data):
    if len(data) < INNER.size:
        raise IOError("Truncated Nspire packet")
    magic, src_addr, source, dst_addr, destination, crc, size, ack, sequence, checksum = (
        INNER.unpack_from(data)
    )
    if magic != 0x54FD or sum(data[:15]) & 255 != checksum:
        raise IOError("Invalid Nspire packet header")
    body = data[16:]
    if size == 255:
        if len(body) < 4:
            raise IOError("Truncated Nspire extended packet")
        size = struct.unpack_from(">I", body)[0]
        expected = size + 4
        payload = body[4:]
    else:
        expected, payload = size, body
    if size > 1440 or len(body) != expected or data_checksum(body) != crc:
        raise IOError("Invalid Nspire packet payload")
    return source, destination, ack, sequence, payload


def outer_packet(
    service, data=b"", sequence=0, source=0xFE, destination=1, req_ack=0, misc=0, unknown=0
):
    header = OUTER.pack(
        misc, service, source, destination, unknown, req_ack, 12 + len(data), sequence, 0
    )
    message = header + data
    return message[:10] + struct.pack(">H", internet_checksum(message)) + data


def unpad_outer_transfer(data):
    """Accept the CX II transfer pad only on a complete checksummed envelope.

    Native CX II messages whose declared length is a multiple of 64 carry one
    extra USB byte; its value is unspecified. Zero trailers have also been
    observed on file transfers. Never scan for a header or discard bytes from
    a partial/concatenated envelope to recover an invalid stream.
    """
    if len(data) >= 13:
        size = struct.unpack_from(">H", data, 6)[0]
        if (
            12 <= size <= 1484
            and len(data) == size + 1
            and (size % 64 == 0 or data[-1] == 0)
            and internet_checksum(data[:size]) == 0
        ):
            return data[:size]
    return data


class NspireProtocol:
    def __init__(self, io, cx2=True):
        self.io, self.cx2 = io, cx2
        # Native channels are keyed by this endpoint. Do not give a new session
        # an old channel's address while its dispatch wrapper is still deleting it.
        self.host = 0x8000 + next(_host_ports) % 0x7FFF
        self.sequence = 0
        self.inner_sequence = 1
        self.incoming = deque()
        self.buffer = bytearray()
        self.connected = False
        self.ready = False
        self.peer_finished = False
        self.pause_peer_close = False

    def send_outer(self, service, data=b"", req_ack=0):
        sequence = self.sequence
        self.sequence = (sequence + 1) & 65535
        self.io.write(outer_packet(service, data, sequence, req_ack=req_ack))
        return sequence

    def receive_outer(self, deadline):
        while len(self.buffer) < 12:
            data = self.io.read(2048, max(1, (deadline - time.monotonic()) * 1000))
            # Only examine a whole USB transfer at an empty envelope boundary.
            # A fragment's leading bytes are not necessarily a header.
            self.buffer.extend(unpad_outer_transfer(data) if not self.buffer else data)
        size = struct.unpack_from(">H", self.buffer, 6)[0]
        if not 12 <= size <= 1484:
            raise IOError(
                f"Invalid CX II USB message length ({size}; buffered={len(self.buffer)}; "
                f"header={bytes(self.buffer[:12]).hex()})"
            )
        while len(self.buffer) < size:
            self.buffer.extend(self.io.read(2048, max(1, (deadline - time.monotonic()) * 1000)))
        message = bytes(self.buffer[:size])
        del self.buffer[:size]
        if internet_checksum(message):
            raise IOError("CX II USB message checksum mismatch")
        misc, service, source, destination, unknown, req_ack, _, sequence, _ = OUTER.unpack_from(
            message
        )
        if destination not in (0xFE, 0xFF):
            return None
        if service & 0x80:
            return service, sequence
        if req_ack & 1:
            self.io.write(
                outer_packet(
                    service | 0x80,
                    sequence=sequence,
                    source=destination,
                    destination=source,
                    req_ack=req_ack & ~1,
                    misc=misc,
                    unknown=unknown,
                )
            )
        data = message[12:]
        if service == 1 and len(data) >= 65 and data[0] == 0:
            self.send_outer(1, b"\x80")
            self.send_outer(1, b"\x80")
        elif service == 2 and data == b"\0":
            self.send_outer(2, b"\x80" + struct.pack(">I", int(time.time())) + bytes(12))
            self.ready = True
        elif service == 8 and data == b"\x01":
            self.send_outer(8, b"\x81\x03")
        elif service == 4:
            if len(self.incoming) >= 32:
                raise IOError("Calculator USB receive queue overflow")
            self.incoming.append(data)
        return None

    def send(self, data, source=None, destination=None):
        source = self.host if source is None else source
        destination = SERVICE if destination is None else destination
        message = packet(
            data,
            source,
            destination,
            0 if self.cx2 else self.inner_sequence,
            device_address=0xC801 if self.cx2 else 0x6401,
        )
        deadline = time.monotonic() + 1.5
        if self.cx2:
            sequence = self.send_outer(4, message, req_ack=1)
            while time.monotonic() < deadline:
                if self.receive_outer(deadline) == (0x84, sequence):
                    return
        else:
            self.io.write(message)
            while time.monotonic() < deadline:
                response = self.receive(deadline)
                src, dst, _, sequence, _ = unpack_packet(response)
                if dst == self.host and src in (0xFE, 0xFF) and sequence == self.inner_sequence:
                    self.inner_sequence = (self.inner_sequence % 255) + 1
                    return
                self.handle_inner(response)
        raise TimeoutError("Calculator did not acknowledge USB packet")

    def receive(self, deadline):
        if not self.cx2:
            return self.io.read(270, max(1, (deadline - time.monotonic()) * 1000))
        while not self.incoming and time.monotonic() < deadline:
            self.receive_outer(deadline)
        if not self.incoming:
            raise TimeoutError("Calculator did not return USB data")
        return self.incoming.popleft()

    def handle_inner(self, message):
        source, destination, ack, sequence, payload = unpack_packet(message)
        if not self.cx2:
            ack_source = (
                (0xFF if sequence else 0xFE) if destination in (self.host, 0x40DE) else 0xD3
            )
            header = packet(
                struct.pack(">H", destination), ack_source, source, sequence, 10, 0x6401
            )
            self.io.write(header)
        if source in (0xFE, 0xFF, 0xD3) or ack:
            return None
        if destination == self.host:
            return payload
        return None

    def begin(self):
        if self.connected:
            raise OSError("Calculator channel is already open")
        if self.cx2 and not self.ready:
            self.send_outer(
                1, b"\x01"
            )  # Initialize this physical USB connection once, not every control RPC.
            deadline = time.monotonic() + 1.5
            while not self.ready and time.monotonic() < deadline:
                self.receive_outer(deadline)
            if not self.ready:
                raise TimeoutError("Calculator USB handshake did not complete")
        elif not self.cx2 and not self.ready:
            self.receive(time.monotonic() + 1.5)  # Native CX address request.
            self.send(b"\x64\x01\xff\0", source=0x4003, destination=0x4003)
            self.ready = True
        self.connected = True
        return True

    def write(self, data):
        if not self.connected or len(data) > (1440 if self.cx2 else 254):
            raise IOError("Invalid calculator channel or packet size")
        self.send(data)

    def read(self):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            payload = self.handle_inner(self.receive(deadline))
            if payload is not None:
                if not 32 <= len(payload) <= MAX_REPLY:
                    raise IOError("Incomplete screen-mirror reply")
                return payload
        raise TimeoutError("Calculator did not reply to screen-mirror request")

    def exchange(self, data):
        self.write(data)
        reply = self.read()
        if (
            len(data) == 32
            and data[:4] == b"NDM1"
            and data[5] == CAPS
            and reply[:4] == b"NDA1"
            and reply[5] == OK
            and reply[8:12] == data[8:12]
        ):
            self.pause_peer_close = bool(
                struct.unpack_from("<I", reply, 24)[0] & CAP_PAUSE_PEER_CLOSE
            )
        if (
            len(data) == 32
            and data[:4] == b"NDM1"
            and data[5] == PAUSE
            and reply[:4] == b"NDA1"
            and reply[5] == OK
            and reply[7] == 1
            and reply[8:12] == data[8:12]
        ):
            self.peer_finished = not self.pause_peer_close
        return reply

    def batch(self, packets):
        if not packets:
            raise IOError("Empty frame batch")
        for data in packets:
            self.write(data)
        return self.read()

    def end(self):
        if self.connected:
            try:
                if not self.peer_finished:
                    self.send(struct.pack(">H", self.host), source=0x40DE)
                if self.peer_finished or self.pause_peer_close:
                    # Legacy PAUSE retires its callback itself. New receivers
                    # retain it until our ECLOSE. In both cases keep servicing
                    # final native messages before releasing the physical I/O.
                    deadline = time.monotonic() + 0.1
                    while time.monotonic() < deadline:
                        try:
                            self.handle_inner(self.receive(deadline))
                        except TimeoutError:
                            break
            finally:
                self.connected = False
                self.peer_finished = False
                self.host = 0x8000 + next(_host_ports) % 0x7FFF


class WindowsUSB:
    def __init__(self, path=None):
        if path:
            raise ValueError("The direct Windows USB backend does not load an external library")
        self.protocol = None

    def begin(self):
        if self.protocol:
            return self.protocol.begin()
        from winusb import WinUSB, paths

        failures = []
        for path in paths():
            io = None
            try:
                io = WinUSB(path)
                protocol = NspireProtocol(io, "pid_e022" in path.lower())
                protocol.begin()
                self.protocol = protocol
                return True
            except OSError as error:
                if io:
                    io.close()
                failures.append(str(error))
        if failures:
            raise IOError("Calculator USB could not be opened: " + failures[0])
        return False

    def exchange(self, data):
        return self.protocol.exchange(data)

    def batch(self, packets):
        return self.protocol.batch(packets)

    def end(self):
        if self.protocol:
            protocol = self.protocol
            try:
                protocol.end()
            except BaseException:
                self.protocol = None
                protocol.io.close()
                raise

    def close(self):
        protocol, self.protocol = self.protocol, None
        if protocol:
            try:
                protocol.end()
            finally:
                protocol.io.close()
