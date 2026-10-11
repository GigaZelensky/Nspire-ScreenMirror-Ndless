"""Transfer calculator files over USB, independently of the mirror sender.

File-service protocol reference: Vogtinator/libnspire, revision
7d7d962ca49ff9017f6f146336c51ef7f2edf4e0 (GPL-3.0-or-later).
Directory enumeration and downloads are read-only. The explicit put command
backs up an existing destination before uploading and verifies by reading back.
Calculator files are never renamed or deleted by this tool.
"""

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import secrets
from pathlib import Path, PurePosixPath
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from nspire_usb import NspireProtocol, unpack_packet, unpad_outer_transfer

FILE_SERVICE = 0x4060
MAX_FILE_BYTES = 8 * 1024 * 1024


def wire_string(value):
    raw = value.encode("utf-8")
    if b"\0" in raw or not 0 < len(raw) <= 239:
        raise ValueError("Calculator path must contain 1 to 239 UTF-8 bytes")
    return raw + bytes(max(8, len(raw)) + 1 - len(raw))


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    modified: int
    directory: bool


def directory_entry(data):
    if len(data) < 3:
        raise IOError("Truncated directory entry")
    end = data.find(b"\0", 3)
    if end < 0 or end - 3 > 239:
        raise IOError("Invalid directory name")
    name = data[3:end].decode("utf-8")
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise IOError("Unsafe directory name")
    offset = 3 + max(8, end - 3) + 1
    if len(data) != offset + 10 or any(data[end:offset]):
        raise IOError("Invalid directory entry length or padding")
    size, modified, kind, zero = struct.unpack_from(">IIBB", data, offset)
    if kind not in (0, 1) or zero:
        raise IOError("Invalid directory entry type")
    return Entry(name, size, modified, bool(kind))


class FileClient:
    def __init__(self, protocol):
        self.protocol = protocol

    def send(self, data):
        self.protocol.send(data, destination=FILE_SERVICE)

    def receive(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            message = self.protocol.receive(deadline)
            source, destination, ack, sequence, data = unpack_packet(message)
            # handle_inner also acknowledges packets on classic CX.
            payload = self.protocol.handle_inner(message)
            if destination == self.protocol.host and not ack and source == 0x40DE:
                raise IOError("Calculator closed the file-service channel")
            if payload is not None:
                return payload
        raise TimeoutError("Calculator file service did not reply")

    @contextmanager
    def session(self):
        def finish():
            self.protocol.send(
                struct.pack(">H", self.protocol.host),
                source=0x40DE,
                destination=FILE_SERVICE,
            )

        try:
            yield
        except BaseException:
            try:
                finish()
            except OSError:
                pass  # Preserve the transfer failure if cleanup also fails.
            raise
        else:
            finish()
        finally:
            # ECLOSE identifies the host endpoint in its payload. Do not call
            # the mirror-specific protocol.end(), which targets service 0x4008.
            self.protocol.host = 0x8000 + ((self.protocol.host - 0x8000 + 1) % 0x7FFF)

    def list_directory(self, path):
        entries = []
        with self.session():
            self.send(b"\x0d" + wire_string(path) + b"\0")
            response = self.receive()
            if response != b"\xff\0":
                raise IOError(f"Directory open failed: {response.hex()}")
            for _ in range(1024):
                self.send(b"\x0e")
                response = self.receive()
                if response in (b"\xff\0", b"\xff\x11"):
                    break
                if response[:1] not in (b"\x0e", b"\x10"):
                    raise IOError(f"Unexpected directory response: {response[:16].hex()}")
                entries.append(directory_entry(response))
            else:
                raise IOError("Calculator directory exceeds 1024 entries")
            self.send(b"\x0f")
            if self.receive() != b"\xff\0":
                raise IOError("Directory enumeration could not finish")
        return entries

    def download(self, path, maximum=MAX_FILE_BYTES):
        with self.session():
            self.send(b"\x07\x01" + wire_string(path))
            response = self.receive()
            if response[:2] != b"\x03\x01":
                if response[:1] == b"\xff":
                    raise FileNotFoundError(
                        f"Calculator file not available: {path} ({response.hex()})"
                    )
                raise IOError(f"Unexpected download header: {response[:24].hex()}")
            if len(response) != 15 or any(response[2:11]):
                raise IOError("Invalid download header")
            length = struct.unpack_from(">I", response, 11)[0]
            if length > maximum:
                raise IOError(f"Calculator file exceeds the {maximum}-byte retrieval limit")
            self.send(b"\x04")
            data = bytearray()
            while len(data) < length:
                response = self.receive()
                if not response or response[0] != 5 or len(response) < 2:
                    raise IOError(f"Invalid download data packet: {response[:16].hex()}")
                if len(response) - 1 > length - len(data):
                    raise IOError("Download exceeds advertised file length")
                data.extend(response[1:])
            self.send(b"\xff\0")
        return bytes(data)

    def upload(self, path, data):
        if not 0 < len(data) <= MAX_FILE_BYTES:
            raise ValueError("Upload must contain 1 byte to 8 MiB")
        with self.session():
            self.send(b"\x03\x01" + wire_string(path) + struct.pack(">I", len(data)))
            response = self.receive()
            if response != b"\x04":
                raise IOError(f"Calculator refused upload: {response.hex()}")
            limit = 1439 if self.protocol.cx2 else 253
            for offset in range(0, len(data), limit):
                self.send(b"\x05" + data[offset : offset + limit])
            response = self.receive()
            if response != b"\xff\0":
                raise IOError(f"Calculator could not finish upload: {response.hex()}")
        received = self.download(path)
        if received != data:
            raise IOError("Calculator read-back differs from the uploaded file")
        return len(data)

    def find(self, filename, maximum_directories=80):
        pending = [("/", 0)]
        visited = set()
        while pending:
            directory, depth = pending.pop(0)
            if directory in visited:
                continue
            if len(visited) >= maximum_directories:
                raise IOError("Directory search limit reached")
            visited.add(directory)
            for item in self.list_directory(directory):
                path = str(PurePosixPath(directory) / item.name)
                if not item.directory and item.name.casefold() == filename.casefold():
                    yield path
                elif item.directory and depth < 8:
                    pending.append((path, depth + 1))


def file_usb_message(data):
    """Use the same complete-envelope transfer padding check as streaming."""
    return unpad_outer_transfer(data)


class FileUSB:
    def __init__(self, io, trace=None):
        self.io, self.trace = io, trace

    def record(self, direction, data):
        if self.trace is None:
            return
        self.trace.write(
            json.dumps({"direction": direction, "data": data.hex(), "time": time.monotonic()})
            + "\n"
        )
        self.trace.flush()

    def read(self, *args):
        data = self.io.read(*args)
        self.record("in", data)
        return file_usb_message(data)

    def write(self, data):
        self.record("out", data)
        return self.io.write(data)

    def close(self):
        self.io.close()


@contextmanager
def connected_calculator(trace=None):
    from winusb import WinUSB, paths
    from transport import linking_apps

    if linking_apps():
        raise RuntimeError("Close TI-Nspire PC software before using direct USB")
    candidates = list(paths())
    if not candidates:
        raise RuntimeError("No connected Nspire USB interface found")
    errors = []
    for path in candidates:
        io = None
        try:
            io = FileUSB(WinUSB(path), trace)
            protocol = NspireProtocol(io, "pid_e022" in path.lower())
            protocol.begin()
            # A failed transfer may still have a late native response queued.
            # Use a fresh endpoint so it cannot be mistaken for this download.
            protocol.host = 0x8000 + secrets.randbelow(0x7FFF)
        except OSError as error:
            if io:
                io.close()
            errors.append(str(error))
            continue
        try:
            yield FileClient(protocol)
        finally:
            io.close()
        return
    raise IOError("Calculator could not be opened: " + "; ".join(errors))


def save_download(client, remote, output):
    print(f"Retrieving {remote}", flush=True)
    data = client.download(remote)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    name = PurePosixPath(remote).name
    suffix = ".tns" if name.lower().endswith(".tns") else ""
    stem = name[: -len(suffix)] if suffix else name
    output.mkdir(parents=True, exist_ok=True)
    local = output / f"{stem}-usb-{stamp}{suffix}"
    with local.open("xb") as file:
        file.write(data)
    metadata = {
        "calculator_path": remote,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "retrieved_utc": stamp,
        "local_path": str(local.resolve()),
    }
    with local.with_suffix(local.suffix + ".json").open("x", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)
        file.write("\n")
    print(json.dumps(metadata), flush=True)
    return local


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, help="Save raw USB diagnostics to a new file")
    commands = parser.add_subparsers(dest="command", required=True)
    ls = commands.add_parser("list", help="List one calculator directory")
    ls.add_argument("path", nargs="?", default="/")
    download = commands.add_parser("get", help="Download one known calculator file")
    download.add_argument("path")
    download.add_argument("--output", type=Path, required=True)
    upload = commands.add_parser("put", help="Back up, upload, and verify one calculator file")
    upload.add_argument("local", type=Path)
    upload.add_argument("path")
    upload.add_argument("--backup", type=Path, required=True)
    find = commands.add_parser("find-log", help="Find and download a saved diagnostic log")
    find.add_argument("--name", default="ndmirror.log.tns")
    find.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trace_context = args.trace.open("x", encoding="utf-8") if args.trace else nullcontext()
    with trace_context as trace, connected_calculator(trace) as client:
        if args.command == "list":
            for entry in client.list_directory(args.path):
                print(json.dumps(entry.__dict__), flush=True)
        elif args.command == "get":
            save_download(client, args.path, args.output)
        elif args.command == "put":
            if not args.local.is_file() or not 0 < args.local.stat().st_size <= MAX_FILE_BYTES:
                parser.error("Upload must contain 1 byte to 8 MiB")
            data = args.local.read_bytes()
            try:
                backup = save_download(client, args.path, args.backup)
            except FileNotFoundError:
                backup = None
            length = client.upload(args.path, data)
            print(
                json.dumps(
                    {
                        "calculator_path": args.path,
                        "bytes": length,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "verified_readback": True,
                        "backup": str(backup.resolve()) if backup else None,
                    }
                ),
                flush=True,
            )
        else:
            found = False
            for path in client.find(args.name):
                found = True
                save_download(client, path, args.output)
            if not found:
                raise FileNotFoundError(f"No saved {args.name} found on the calculator")


if __name__ == "__main__":
    main()
