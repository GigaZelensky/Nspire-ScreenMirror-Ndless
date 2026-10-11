"""Portal lifecycle/fd ownership and bounded capture, without sharing a real desktop."""

import argparse
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from wayland_capture import PortalSession, WaylandCapture, pipeline, DESKTOP, REQUEST, SESSION
from capture import create_capture


class Variant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value


class Message:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


instances = []


class Bus:
    pending = False
    declined = False
    fail_open = False

    def __init__(self, negotiate_unix_fd):
        assert negotiate_unix_fd
        self.unique_name = ":1.42"
        self.calls = []
        self.closed = False
        self.fd = None
        instances.append(self)

    async def connect(self):
        return self

    def add_message_handler(self, handler):
        self.handler = handler

    def disconnect(self):
        self.closed = True

    async def wait_for_disconnect(self):
        while not self.closed:
            await asyncio.sleep(0.01)

    async def call(self, message):
        self.calls.append(message)
        reply = SimpleNamespace(message_type=1, body=[], unix_fds=[])
        if message.member == "Get":
            reply.body = [Variant("u", 3)]
        elif message.member in ("CreateSession", "SelectSources", "Start"):
            options = message.body[-1]
            token = options["handle_token"].value
            path = f"{DESKTOP}/request/1_42/{token}"
            reply.body = [path]
            values = {}
            if message.member == "CreateSession":
                assert message.signature == "a{sv}"
                self.session = f"{DESKTOP}/session/1_42/" + options["session_handle_token"].value
                values = {"session_handle": Variant("s", self.session)}
            elif message.member == "SelectSources":
                assert message.signature == "oa{sv}" and options["types"].value == 1
                assert not options["multiple"].value and options["cursor_mode"].value == 2
            elif message.member == "Start":
                assert message.signature == "osa{sv}"
                values = {
                    "streams": Variant(
                        "a(ua{sv})",
                        [
                            [
                                42,
                                {
                                    "size": Variant("(ii)", [1920, 1080]),
                                    "pipewire-serial": Variant("t", 123456),
                                },
                            ]
                        ],
                    )
                }
                if self.pending:
                    return reply
            # Response intentionally precedes the method reply (portal race).
            self.handler(
                Message(
                    message_type=4,
                    interface=REQUEST,
                    member="Response",
                    path=path,
                    body=[1 if self.declined else 0, values],
                )
            )
        elif message.member == "OpenPipeWireRemote":
            if self.fail_open:
                reply.message_type = 3
                reply.error_name = "org.test.PipeWireUnavailable"
            else:
                self.fd, other = os.pipe()
                os.close(other)
                reply.body, reply.unix_fds = [0], [self.fd]
        return reply


dbus = ModuleType("dbus_next")
dbus.Message, dbus.Variant = Message, Variant
dbus.MessageType = SimpleNamespace(SIGNAL=4, ERROR=3)
aio = ModuleType("dbus_next.aio")
aio.MessageBus = Bus
with patch.dict(sys.modules, {"dbus_next": dbus, "dbus_next.aio": aio}):
    portal = PortalSession()
    bus = instances[-1]
    assert portal.region == dict(left=0, top=0, width=1920, height=1080)
    assert portal.node == 42 and portal.target == "123456" and os.fstat(portal.fd)
    assert portal.target_property == "target-object"
    fd = portal.fd
    portal.close()
    assert bus.closed and not portal.thread.is_alive()
    assert any(m.member == "Close" and m.interface == SESSION for m in bus.calls)
    try:
        os.fstat(fd)
        raise AssertionError("Portal fd leaked")
    except OSError:
        pass
    Bus.declined = True
    try:
        PortalSession()
        raise AssertionError("Declined sharing proceeded")
    except OSError as error:
        assert "declined" in str(error) and instances[-1].closed
    Bus.declined = False
    Bus.fail_open = True
    try:
        PortalSession()
        raise AssertionError("Missing PipeWire remote was hidden")
    except OSError as error:
        assert "OpenPipeWireRemote" in str(error) and instances[-1].closed
        assert any(m.interface == SESSION and m.member == "Close" for m in instances[-1].calls)
    Bus.fail_open = False
    Bus.pending = True
    stop = threading.Event()
    timer = threading.Timer(0.5, stop.set)
    timer.start()
    try:
        PortalSession(stop)
        raise AssertionError("Stop did not cancel sharing approval")
    except InterruptedError:
        assert instances[-1].closed
        assert any(m.interface == REQUEST and m.member == "Close" for m in instances[-1].calls)
    finally:
        timer.cancel()
    Bus.pending = False
    portal = PortalSession()
    bus = instances[-1]
    assert portal.session == bus.session
    # Closed signal handling does not need to wait for the next video buffer.
    portal._signal(Message(message_type=4, interface=SESSION, member="Closed", path=bus.session))
    portal.close()
    assert portal.closed and "ended" in str(portal.error)

# Wayland must select the portal before MSS opens an X display, even with DISPLAY set.
args = argparse.Namespace(capture=None, region=None, monitor=1)
with (
    patch("capture.os.name", "posix"),
    patch.dict(
        os.environ,
        {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-test", "DISPLAY": ":99"},
    ),
    patch("wayland_capture.WaylandCapture", return_value="portal") as factory,
):
    assert create_capture(args) == "portal" and factory.call_args.args == (args,)
args.capture = "wayland"
with patch("capture.os.name", "nt"):
    try:
        create_capture(args)
        raise AssertionError("Wayland backend used on Windows")
    except ValueError:
        pass

session = PortalSession.__new__(PortalSession)
session.stop = threading.Event()
session.quit = threading.Event()
session.closed = False


async def timeout():
    try:
        await session._wait(asyncio.sleep(10), timeout=0.01)
        raise AssertionError("Portal wait unbounded")
    except TimeoutError:
        pass


asyncio.run(timeout())
print(
    "PASS Wayland auto-selection, portal request race/denial/cancellation, session revocation and fd cleanup"
)

if sys.platform == "linux":
    subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(Path(__file__).with_name("wayland_bus.py"))],
        check=True,
        timeout=15,
    )
    executable = shutil.which("gst-launch-1.0")
    assert executable, "Install gstreamer1.0-tools and plugins-base for Linux integration checks"
    source = [
        "videotestsrc",
        "num-buffers=3",
        "pattern=red",
        "!",
        "video/x-raw,format=RGB,width=320,height=180,pixel-aspect-ratio=1/1",
    ]
    result = subprocess.run([executable, "-q", *pipeline(source)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert (
        len(result.stdout) % (320 * 240 * 3) == 0
        and 1 <= len(result.stdout) // (320 * 240 * 3) <= 3
    )
    assert result.stdout[: 320 * 30 * 3] == bytes(320 * 30 * 3), "Letterboxing lost"
    assert result.stdout[320 * 60 * 3 : 320 * 60 * 3 + 3] == bytes([255, 0, 0]), (
        "RGB channels changed"
    )
    assert result.stdout[320 * 210 * 3 : 320 * 240 * 3] == bytes(320 * 30 * 3)

    class SyntheticPortal:
        def __init__(self, stop):
            self.region = dict(left=0, top=0, width=320, height=180)
            self.fd, self.writer = os.pipe()
            self.target = "42"
            self.target_property = "path"
            self.error = None
            self.closed = False

        def close(self):
            os.close(self.fd)
            os.close(self.writer)
            self.closed = True

    process = subprocess.Popen(
        [
            executable,
            "-q",
            *pipeline(
                [
                    "videotestsrc",
                    "num-buffers=300",
                    "is-live=true",
                    "pattern=red",
                    "!",
                    "video/x-raw,width=320,height=180,pixel-aspect-ratio=1/1",
                ]
            ),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        with patch("wayland_capture.subprocess.Popen", return_value=process):
            capture = WaylandCapture(
                argparse.Namespace(region=None), portal_factory=SyntheticPortal
            )
        portal = capture.portal
        image = capture.grab()
        assert image.size == (320, 240) and image.getpixel((100, 100)) == (255, 0, 0)
        capture.stop.set()
        try:
            capture.grab()
            raise AssertionError("Stopped capture still emitted frames")
        except InterruptedError:
            pass
        capture.close()
        assert portal.closed and process.poll() is not None and not capture.reader.is_alive()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(2)
    print(
        "PASS real Linux GStreamer RGB/letterboxing, fragmented pipe frames, latest frame and process shutdown"
    )
