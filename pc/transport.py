"""Native transport lives in a killable helper; stalled vendor calls cannot trap the GUI."""

import ctypes as c
import ctypes.util
import contextlib
import multiprocessing as mp
import os
from pathlib import Path
import struct
import time
from wire import SERVICE, MAX_REPLY
from nspire_usb import WindowsUSB


def linking_apps():
    """TI's linking app owns the same native connection; never terminate it."""
    if os.name != "nt":
        return []
    from ctypes import wintypes as w

    class Entry(c.Structure):
        _fields_ = [
            ("size", w.DWORD),
            ("usage", w.DWORD),
            ("pid", w.DWORD),
            ("heap", c.c_size_t),
            ("module", w.DWORD),
            ("threads", w.DWORD),
            ("parent", w.DWORD),
            ("priority", w.LONG),
            ("flags", w.DWORD),
            ("name", w.WCHAR * 260),
        ]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [w.DWORD, w.DWORD]
    snapshot.restype = w.HANDLE
    first = kernel.Process32FirstW
    first.argtypes = [w.HANDLE, c.POINTER(Entry)]
    first.restype = w.BOOL
    next_process = kernel.Process32NextW
    next_process.argtypes = first.argtypes
    next_process.restype = w.BOOL
    close = kernel.CloseHandle
    close.argtypes = [w.HANDLE]
    close.restype = w.BOOL
    handle = snapshot(2, 0)
    if handle in (None, c.c_void_p(-1).value):
        return []
    names = []
    entry = Entry()
    entry.size = c.sizeof(entry)
    try:
        valid = first(handle, c.byref(entry))
        while valid:
            if entry.name.lower().startswith("ti-nspire"):
                names.append(entry.name)
            valid = next_process(handle, c.byref(entry))
    finally:
        close(handle)
    return names


class WindowsNavNet:
    def __init__(self, path=None):
        path = path or os.environ.get("NDUSB_NAVNET")
        if not path:
            candidates = []
            for base in ("ProgramFiles", "ProgramFiles(x86)"):
                directory = Path(os.environ.get(base, "C:/Program Files")) / "TI Education"
                for candidate in directory.glob("**/navnet.dll"):
                    with candidate.open("rb") as f:
                        f.seek(60)
                        offset = struct.unpack("<I", f.read(4))[0]
                        f.seek(offset + 4)
                        if f.read(2) == b"\x64\x86":
                            install = candidate.parent.parent
                            complete = (install / "bridgelink.dll").is_file() and (
                                install / "bridgelinkserver.exe"
                            ).is_file()
                            candidates.append((complete, str(candidate)))
            if candidates:
                # Several TI products can coexist. Student installs may have
                # navnet.dll but lack the server required by the CX II bridge.
                candidates.sort(key=lambda item: (not item[0], item[1].lower()))
                path = candidates[0][1]
        if not path:
            raise RuntimeError(
                "Install 64-bit TI-Nspire PC software, or set NDUSB_NAVNET to its navnet.dll."
            )
        path = Path(path).resolve()
        # The CX II connector loads bridgelink.dll and launches its sibling
        # server. Preserve the installed root/lib search paths in this helper.
        install = path.parent.parent
        os.chdir(install)
        search = [install, path.parent, path.parent / "connectors"]
        os.environ["PATH"] = (
            os.pathsep.join(map(str, search)) + os.pathsep + os.environ.get("PATH", "")
        )
        self.search_dirs = [os.add_dll_directory(str(p)) for p in search if p.is_dir()]
        self.bridge_library = (
            c.CDLL(str(install / "bridgelink.dll"), winmode=8)
            if (install / "bridgelink.dll").is_file()
            else None
        )
        self.library = c.CDLL(str(path), winmode=8)

        def bind(name, args, result=c.c_int16):
            f = getattr(self.library, "TI_NN_" + name)
            f.argtypes = args
            f.restype = result
            return f

        self.init = bind("Init", [c.c_char_p])
        self.shutdown = bind("Shutdown", [])
        self.create = bind("CreateOperationHandle", [], c.c_void_p)
        self.destroy = bind("DestroyOperationHandle", [c.c_void_p])
        self.enum_init = bind("NodeEnumInit", [c.c_void_p])
        self.enum_done = bind("NodeEnumDone", [c.c_void_p])
        self.next = bind("NodeEnumNext", [c.c_void_p, c.POINTER(c.c_void_p)])
        self.connect = bind("Connect", [c.c_void_p, c.c_uint32, c.POINTER(c.c_void_p)])
        self.disconnect = bind("Disconnect", [c.c_void_p])
        self.write = bind("Write", [c.c_void_p, c.c_void_p, c.c_uint32])
        self.read = bind(
            "Read", [c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32)]
        )
        notify_type = c.CFUNCTYPE(None)
        self.notify = notify_type(lambda: None)
        self.register = bind("RegisterNotifyCallback", [c.c_uint32, notify_type])
        self.channel = c.c_void_p()
        self.initialized = False
        options = os.environ.get("NDMIRROR_NAVNET_OPTIONS", "-c 0 -d 0").encode()
        self.check(self.init(options), "TI USB initialization")
        self.initialized = True
        try:
            self.check(self.register(0, self.notify), "notification registration")
        except Exception:
            self.shutdown()
            self.initialized = False
            raise

    @staticmethod
    def check(rc, stage):
        if rc < 0:
            raise IOError(f"{stage} failed ({rc})")

    def begin(self):
        op = self.create()
        node = c.c_void_p()
        started = False
        if not op:
            raise IOError("Could not create enumeration handle")
        try:
            rc = self.enum_init(op)
            if rc == -274:
                return False
            self.check(rc, "calculator discovery")
            started = True
            rc = self.next(op, c.byref(node))
            if rc == -274:
                return False
            self.check(rc, "calculator lookup")
            if not node:
                return False
            self.check(
                self.connect(node, SERVICE, c.byref(self.channel)), "screen-mirror connection"
            )
            if not self.channel:
                raise IOError("No screen-mirror channel")
            return True
        finally:
            if started:
                self.enum_done(op)
            self.destroy(op)

    def exchange(self, data):
        if not self.channel or len(data) > 1440:
            raise IOError("Invalid channel or packet")
        request = c.create_string_buffer(data)
        self.check(self.write(self.channel, request, len(data)), "USB write")
        reply = c.create_string_buffer(MAX_REPLY)
        length = c.c_uint32()
        self.check(self.read(self.channel, 1500, reply, len(reply), c.byref(length)), "USB read")
        if not 32 <= length.value <= MAX_REPLY:
            raise IOError("Incomplete screen-mirror reply")
        return reply.raw[: length.value]

    def batch(self, packets):
        if not packets:
            raise IOError("Empty frame batch")
        for data in packets:
            if len(data) > 1440:
                raise IOError("Packet too large")
            request = c.create_string_buffer(data)
            self.check(self.write(self.channel, request, len(data)), "USB write")
        reply = c.create_string_buffer(MAX_REPLY)
        length = c.c_uint32()
        self.check(
            self.read(self.channel, 1500, reply, len(reply), c.byref(length)), "USB frame reply"
        )
        if not 32 <= length.value <= MAX_REPLY:
            raise IOError("Incomplete frame reply")
        return reply.raw[: length.value]

    def end(self):
        if self.channel:
            channel = self.channel
            self.channel = c.c_void_p()
            self.check(self.disconnect(channel), "USB disconnect")

    def close(self):
        try:
            self.end()
        finally:
            if self.initialized:
                self.shutdown()
                self.initialized = False


class LinuxNspire:
    def __init__(self, path=None):
        path = path or os.environ.get("NDMIRROR_LIBNSPIRE") or ctypes.util.find_library("nspire")
        if not path:
            raise RuntimeError(
                "Install/build Vogtinator/libnspire (CX II support), or set NDMIRROR_LIBNSPIRE."
            )
        self.lib = c.CDLL(path)
        self.handle = c.c_void_p()
        self.connected = False

        def bind(name, args, result=c.c_int):
            f = getattr(self.lib, name)
            f.argtypes = args
            f.restype = result
            return f

        self.init = bind("nspire_init", [c.POINTER(c.c_void_p)])
        self.free = bind("nspire_free", [c.c_void_p], None)
        self.connect = bind("service_connect", [c.c_void_p, c.c_uint16])
        self.disconnect = bind("service_disconnect", [c.c_void_p])
        self.write = bind("data_write", [c.c_void_p, c.c_void_p, c.c_size_t])
        self.read = bind("data_read", [c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)])
        self.maximum = bind("packet_max_datasize", [c.c_void_p], c.c_uint32)
        self.check(self.init(c.byref(self.handle)), "USB initialization")

    @staticmethod
    def check(rc, stage):
        if rc != 0:
            raise IOError(f"{stage} failed ({rc})")

    def begin(self):
        self.check(self.connect(self.handle, SERVICE), "screen-mirror connection")
        self.connected = True
        return True

    def exchange(self, data):
        if not self.connected or len(data) > self.maximum(self.handle):
            raise IOError("Invalid channel or packet")
        request = c.create_string_buffer(data)
        self.check(self.write(self.handle, request, len(data)), "USB write")
        reply = c.create_string_buffer(MAX_REPLY)
        length = c.c_size_t()
        self.check(self.read(self.handle, reply, len(reply), c.byref(length)), "USB read")
        if not 32 <= length.value <= MAX_REPLY:
            raise IOError("Incomplete screen-mirror reply")
        return reply.raw[: length.value]

    def batch(self, packets):
        if not packets:
            raise IOError("Empty frame batch")
        for data in packets:
            if len(data) > self.maximum(self.handle):
                raise IOError("Packet too large")
            request = c.create_string_buffer(data)
            self.check(self.write(self.handle, request, len(data)), "USB write")
        reply = c.create_string_buffer(MAX_REPLY)
        length = c.c_size_t()
        self.check(self.read(self.handle, reply, len(reply), c.byref(length)), "USB frame reply")
        if not 32 <= length.value <= MAX_REPLY:
            raise IOError("Incomplete frame reply")
        return reply.raw[: length.value]

    def end(self):
        if self.connected:
            self.check(self.disconnect(self.handle), "USB disconnect")
            self.connected = False

    def close(self):
        try:
            self.end()
        finally:
            if self.handle:
                self.free(self.handle)
                self.handle = c.c_void_p()


def worker(pipe, backend, path):
    transport = None
    from scheduling import SchedulingLease

    scheduling = None
    try:
        scheduling = SchedulingLease()
        from linux_usb import LinuxUSB

        backend_type = {
            "windows": WindowsUSB,
            "linux": LinuxUSB,
            "navnet": WindowsNavNet,
            "libnspire": LinuxNspire,
        }
        transport = backend_type[backend](path)
        pipe.send(("ok", None))
        while True:
            kind, data = pipe.recv()
            if kind == "close":
                break
            try:
                result = (
                    getattr(transport, kind)(data)
                    if kind in ("exchange", "batch")
                    else getattr(transport, kind)()
                )
                pipe.send(("ok", result))
            except Exception as e:
                pipe.send(("error", str(e)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as e:
        try:
            pipe.send(("error", str(e)))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if transport:
            try:
                transport.close()
            except Exception:
                pass
        pipe.close()
        if scheduling:
            scheduling.close()


class Bridge:
    def __init__(self, stop, backend=None, path=None):
        self.stop = stop
        self.pending = False
        ctx = mp.get_context("spawn")
        self.pipe, child = ctx.Pipe()
        self.process = ctx.Process(
            target=worker,
            args=(child, backend or ("windows" if os.name == "nt" else "linux"), path),
            daemon=True,
        )
        self.process.start()
        child.close()
        try:
            self.receive(15)
        except BaseException:
            self.close()
            raise

    def receive(self, timeout=5, ignore_stop=False):
        deadline = time.monotonic() + timeout
        while not self.pipe.poll(0.025):
            if self.stop.is_set() and not ignore_stop:
                raise InterruptedError("Stopped")
            if not self.process.is_alive():
                raise IOError("USB helper exited")
            if time.monotonic() > deadline:
                raise TimeoutError("USB helper timed out")
        kind, data = self.pipe.recv()
        # An error is still a consumed RPC reply. Do not drain a phantom reply
        # on Stop and accidentally skip PAUSE after a transport error.
        self.pending = False
        if kind != "ok":
            raise IOError(data)
        return data

    def command(self, kind, data=None):
        if self.pending:
            raise OSError("A previous USB request still needs its reply; reconnect required")
        self.pipe.send((kind, data))
        self.pending = True
        result = self.receive()
        self.pending = False
        return result

    def pause_session(self, session, input_ack=0):
        from wire import control, acknowledgement, PAUSE, OK, RESYNC

        # Stop can interrupt a pending batch. Drain its reply before sending
        # the terminal packet, so replies cannot be paired with the wrong RPC.
        if self.pending:
            try:
                self.receive(2, ignore_stop=True)
            except OSError:
                if self.pending:
                    raise
                # The helper returned an error, so the slot is drained. Still
                # try PAUSE rather than leaving an otherwise live receiver active.
        self.pipe.send(("exchange", control(PAUSE, session, input_ack=input_ack)))
        self.pending = True
        state = acknowledgement(self.receive(2, ignore_stop=True), session)
        if state["status"] not in (OK, RESYNC) or state["status"] == OK and not state["need_key"]:
            raise IOError("Calculator did not confirm that the stream paused")

    @contextlib.contextmanager
    def connection(self):
        # -274 is a normal empty enumeration, not a dead USB library. Keep
        # its discovery/notification threads alive until a calculator appears.
        while not self.command("begin"):
            if self.stop.wait(0.15):
                raise InterruptedError("Stopped")
        try:
            yield self
        finally:
            if not self.stop.is_set():
                self.command("end")

    def exchange(self, data):
        return self.command("exchange", data)

    def batch(self, packets):
        return self.command("batch", packets)

    def close(self):
        try:
            if self.process.is_alive():
                try:
                    self.pipe.send(("close", None))
                except (EOFError, BrokenPipeError):
                    pass
                # Disconnect/Shutdown can outlast a frame timeout. Let the TI
                # library release its USB session before terminating a hung helper.
                self.process.join(3)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(2)
        finally:
            self.pipe.close()
