"""ScreenCast portal consent and bounded PipeWire capture, without XWayland scraping."""

import asyncio
from collections import deque
import contextlib
import os
import secrets
import selectors
import shutil
import subprocess
import threading
import time

from PIL import Image

DESTINATION = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"


class PortalSession:
    def __init__(self, stop=None):
        self.stop = stop or threading.Event()
        self.quit = threading.Event()
        self.ready = threading.Event()
        self.closed = False
        self.error = None
        self.bus = None
        self.session = None
        self.fd = None
        self.requests = {}
        self.thread = threading.Thread(target=self._thread, name="ND screen portal", daemon=True)
        self.thread.start()
        while not self.ready.wait(0.05):
            if self.stop.is_set():
                self.close()
                raise InterruptedError("Screen sharing stopped")
        if self.error:
            self.close()
            raise self.error

    async def _wait(self, operation, timeout=180):
        task = asyncio.ensure_future(operation)
        deadline = time.monotonic() + timeout
        try:
            while not task.done():
                if self.stop.is_set() or self.quit.is_set():
                    raise InterruptedError("Screen sharing stopped")
                if self.closed:
                    raise self.error or OSError("Screen sharing ended")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Screen sharing approval timed out")
                await asyncio.sleep(0.05)
            return await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _call(self, member, signature="", body=None, path=DESKTOP, interface=SCREENCAST):
        from dbus_next import Message, MessageType

        reply = await self._wait(
            self.bus.call(
                Message(
                    destination=DESTINATION,
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            ),
            timeout=10,
        )
        if reply.message_type == MessageType.ERROR:
            raise OSError(f"ScreenCast portal {member}: {reply.error_name}. Check docs/linux.md.")
        return reply

    def _signal(self, message):
        from dbus_next import MessageType

        if message.message_type != MessageType.SIGNAL:
            return
        if message.interface == REQUEST and message.member == "Response":
            future = self.requests.get(message.path)
            if future is not None and not future.done():
                future.set_result(message.body)
        elif (
            message.interface == SESSION
            and message.member == "Closed"
            and message.path == self.session
        ):
            self.closed = True
            self.error = OSError("Screen sharing ended in the desktop portal")

    async def _request(self, member, prefix, options):
        from dbus_next import Variant, Message

        token = "ndmirror_" + secrets.token_hex(12)
        sender = self.bus.unique_name[1:].replace(".", "_")
        path = f"{DESKTOP}/request/{sender}/{token}"
        future = asyncio.get_running_loop().create_future()
        self.requests[path] = future  # Install before the call: Response can arrive first.
        options = {**options, "handle_token": Variant("s", token)}
        signature = {"CreateSession": "a{sv}", "SelectSources": "oa{sv}", "Start": "osa{sv}"}[
            member
        ]
        try:
            reply = await self._call(member, signature, [*prefix, options])
            if reply.body != [path]:
                raise OSError("ScreenCast portal returned an unexpected request handle")
            response = await self._wait(future)
            if len(response) != 2 or response[0] != 0:
                raise OSError("Screen sharing was cancelled or declined")
            return response[1]
        finally:
            self.requests.pop(path, None)
            if not future.done() or future.cancelled():
                # Request.Close produces no Response signal.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self.bus.call(
                            Message(
                                destination=DESTINATION,
                                path=path,
                                interface=REQUEST,
                                member="Close",
                            )
                        ),
                        1,
                    )

    async def _run(self):
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus

        disconnected = None
        try:
            self.bus = await self._wait(MessageBus(negotiate_unix_fd=True).connect(), timeout=10)
            disconnected = asyncio.create_task(self.bus.wait_for_disconnect())
            self.bus.add_message_handler(self._signal)
            for interface in (REQUEST, SESSION):
                # Explicit AddMatch is necessary for broadcast Response/Closed signals.
                rule = f"type='signal',sender='{DESTINATION}',interface='{interface}'"
                reply = await self._wait(
                    self.bus.call(
                        Message(
                            destination="org.freedesktop.DBus",
                            path="/org/freedesktop/DBus",
                            interface="org.freedesktop.DBus",
                            member="AddMatch",
                            signature="s",
                            body=[rule],
                        )
                    ),
                    timeout=10,
                )
                if reply.message_type == MessageType.ERROR:
                    raise OSError("Could not subscribe to screen sharing portal signals")
            created = await self._request(
                "CreateSession",
                [],
                {"session_handle_token": Variant("s", "ndmirror_" + secrets.token_hex(12))},
            )
            self.session = created["session_handle"].value
            if not isinstance(self.session, str) or not self.session.startswith(
                DESKTOP + "/session/"
            ):
                raise OSError("ScreenCast portal returned an invalid session")
            modes = await self._call(
                "Get",
                "ss",
                [SCREENCAST, "AvailableCursorModes"],
                interface="org.freedesktop.DBus.Properties",
            )
            if not modes.body[0].value & 2:
                raise OSError("This ScreenCast portal cannot embed the pointer; check its backend")
            await self._request(
                "SelectSources",
                [self.session],
                {
                    "types": Variant("u", 1),
                    "multiple": Variant("b", False),
                    "cursor_mode": Variant("u", 2),
                },
            )
            started = await self._request("Start", [self.session, ""], {})
            streams = started["streams"].value
            if len(streams) != 1:
                raise OSError("Select exactly one monitor for screen sharing")
            self.node, properties = streams[0]
            if not isinstance(self.node, int) or not 0 < self.node < 2**32:
                raise OSError("ScreenCast portal returned an invalid PipeWire node")
            self.target = (
                str(properties["pipewire-serial"].value)
                if "pipewire-serial" in properties
                else str(self.node)
            )
            # target-object takes an object serial, while older portals return a node ID.
            self.target_property = "target-object" if "pipewire-serial" in properties else "path"
            size = properties["size"].value if "size" in properties else (640, 480)
            if len(size) != 2 or any(type(n) is not int or not 0 < n <= 32768 for n in size):
                raise OSError("ScreenCast portal returned invalid monitor dimensions")
            self.region = dict(left=0, top=0, width=size[0], height=size[1])
            remote = await self._call("OpenPipeWireRemote", "oa{sv}", [self.session, {}])
            if remote.body != [0] or len(remote.unix_fds) != 1:
                for fd in remote.unix_fds:
                    os.close(fd)
                raise OSError("ScreenCast portal did not provide one PipeWire descriptor")
            self.fd = remote.unix_fds[0]
            self.ready.set()
            while not self.quit.is_set() and not self.closed:
                if disconnected.done():
                    raise OSError("Screen sharing portal disconnected")
                await asyncio.sleep(0.05)
        finally:
            if self.bus is not None:
                if self.session:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            self.bus.call(
                                Message(
                                    destination=DESTINATION,
                                    path=self.session,
                                    interface=SESSION,
                                    member="Close",
                                )
                            ),
                            1,
                        )
                self.bus.disconnect()
            if disconnected is not None:
                disconnected.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await disconnected
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def _thread(self):
        try:
            asyncio.run(self._run())
        except Exception as error:
            self.error = error
        finally:
            self.closed = True
            self.ready.set()

    def close(self):
        self.quit.set()
        self.thread.join(3)
        if self.thread.is_alive():
            raise TimeoutError("Screen sharing portal did not close")


def pipeline(source):
    return [
        *source,
        "!",
        "queue",
        "max-size-buffers=1",
        "max-size-bytes=0",
        "max-size-time=0",
        "leaky=downstream",
        "!",
        "videoscale",
        "add-borders=true",
        "!",
        "video/x-raw,width=320,height=240,pixel-aspect-ratio=1/1",
        "!",
        "videoconvert",
        "!",
        "video/x-raw,format=RGB",
        "!",
        "fdsink",
        "fd=1",
        "sync=false",
    ]


class WaylandCapture:
    name = "Wayland / PipeWire screen sharing"

    def __init__(self, args, portal_factory=PortalSession):
        executable = shutil.which("gst-launch-1.0")
        if not executable:
            raise OSError("Install GStreamer and its PipeWire plugin; see docs/linux.md")
        if getattr(args, "region", None):
            raise ValueError(
                "Wayland capture selects a monitor in the share dialog; remove --region"
            )
        self.stop = getattr(args, "stop_event", None) or threading.Event()
        self.quit = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.error = None
        self.process = self.portal = None
        self.reader = None
        self.stderr = deque(maxlen=8)
        try:
            self.portal = portal_factory(self.stop)
            self.region = self.portal.region
            source = [
                "pipewiresrc",
                f"fd={self.portal.fd}",
                f"{self.portal.target_property}={self.portal.target}",
            ]
            self.process = subprocess.Popen(
                [executable, "-q", *pipeline(source)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(self.portal.fd,),
                bufsize=0,
            )
            self.errors = threading.Thread(
                target=self._errors, daemon=True, name="ND capture errors"
            )
            self.errors.start()
            self.reader = threading.Thread(
                target=self._read, daemon=True, name="ND PipeWire frames"
            )
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def _errors(self):
        for line in iter(self.process.stderr.readline, b""):
            self.stderr.append(line.decode("utf-8", errors="replace").strip()[:300])

    def _read(self):
        pending = bytearray()
        size = 320 * 240 * 3
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while not self.quit.is_set():
                    if not selector.select(0.1):
                        continue
                    data = os.read(self.process.stdout.fileno(), 65536)
                    if not data:
                        raise OSError("PipeWire capture ended. " + " ".join(self.stderr))
                    pending.extend(data)
                    while len(pending) >= size:
                        frame = Image.frombytes("RGB", (320, 240), bytes(pending[:size]))
                        del pending[:size]
                        with self.lock:
                            self.frame = frame
                        self.ready.set()
        except Exception as error:
            self.error = error
            self.ready.set()

    def grab(self):
        deadline = time.monotonic() + 8
        while not self.ready.wait(0.05):
            if self.stop.is_set():
                raise InterruptedError("Screen sharing stopped")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "PipeWire has not provided a screen frame. " + " ".join(self.stderr)
                )
        if self.stop.is_set():
            raise InterruptedError("Screen sharing stopped")
        if self.error or self.portal.error or self.portal.closed:
            raise self.error or self.portal.error or OSError("Screen sharing ended")
        with self.lock:
            return self.frame  # Static screens may have no new PipeWire buffer.

    def close(self):
        self.quit.set()
        try:
            if self.process is not None:
                self.process.terminate()
                try:
                    self.process.wait(2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(2)
                if self.reader:
                    self.reader.join(2)
                if hasattr(self, "errors"):
                    self.errors.join(2)
                self.process.stdout.close()
                self.process.stderr.close()
                self.process = None
        finally:
            if self.portal is not None:
                try:
                    self.portal.close()
                finally:
                    self.portal = None
