"""Real D-Bus signal routing/Unix-fd negotiation against a private fake portal."""

import asyncio
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from wayland_capture import PortalSession, DESTINATION, DESKTOP, REQUEST


async def run():
    bus = await MessageBus(negotiate_unix_fd=True).connect()
    await bus.request_name(DESTINATION)
    calls = []
    pending = [False]
    descriptors = []

    def handle(message):
        if message.message_type != MessageType.METHOD_CALL or message.destination != DESTINATION:
            return False
        calls.append(message.member)
        if message.member == "Get":
            bus.send(Message.new_method_return(message, "v", [Variant("u", 3)]))
        elif message.member in ("CreateSession", "SelectSources", "Start"):
            options = message.body[-1]
            sender = message.sender[1:].replace(".", "_")
            path = f"{DESKTOP}/request/{sender}/" + options["handle_token"].value
            values = {}
            if message.member == "CreateSession":
                path_session = (
                    f"{DESKTOP}/session/{sender}/" + options["session_handle_token"].value
                )
                values = {"session_handle": Variant("s", path_session)}
            elif message.member == "Start":
                values = {
                    "streams": Variant("a(ua{sv})", [[42, {"size": Variant("(ii)", [640, 480])}]])
                }
            # Broadcast the response first to exercise subscribe-before-call.
            if not (pending[0] and message.member == "Start"):
                bus.send(Message.new_signal(path, REQUEST, "Response", "ua{sv}", [0, values]))
            bus.send(Message.new_method_return(message, "o", [path]))
        elif message.member == "OpenPipeWireRemote":
            read, write = os.pipe()
            os.write(write, b"PW")
            os.close(write)
            descriptors.append(read)
            bus.send(Message.new_method_return(message, "h", [0], unix_fds=[read]))
        elif message.member == "Close":
            bus.send(Message.new_method_return(message))
        else:
            bus.send(Message.new_error(message, "org.test.UnknownMethod", message.member))
        return True

    bus.add_message_handler(handle)
    portal = None
    try:
        portal = await asyncio.to_thread(PortalSession)
        assert os.read(portal.fd, 2) == b"PW", "Unix fd was treated as a descriptor index"
        assert portal.region["width"] == 640 and portal.target == "42"
        assert portal.target_property == "path", (
            "A portal node ID must not be used as an object serial"
        )
        await asyncio.to_thread(portal.close)
        assert "Close" in calls and not portal.thread.is_alive()
        portal = None
        pending[0] = True
        before = calls.count("Start")
        stop = threading.Event()
        creation = asyncio.create_task(asyncio.to_thread(PortalSession, stop))
        for _ in range(200):
            if calls.count("Start") > before:
                break
            await asyncio.sleep(0.01)
        assert calls.count("Start") > before
        stop.set()
        try:
            await asyncio.wait_for(creation, 3)
            raise AssertionError("Real pending D-Bus request did not cancel")
        except InterruptedError:
            pass
        assert calls.count("Close") >= 3
    finally:
        if portal:
            await asyncio.to_thread(portal.close)
        for fd in descriptors:
            os.close(fd)
        bus.disconnect()
    print(
        "PASS private real D-Bus portal signals before reply, Unix-fd passing, session close and pending approval cancellation"
    )


asyncio.run(run())
