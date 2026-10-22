"""Direct access to Nspire USB through the Windows inbox WinUSB driver."""

import ctypes as c
from ctypes import wintypes as w
import uuid


class GUID(c.Structure):
    _fields_ = [("data", c.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le


class Interface(c.Structure):
    _fields_ = [("size", w.DWORD), ("guid", GUID), ("flags", w.DWORD), ("reserved", c.c_size_t)]


class Descriptor(c.Structure):
    _pack_ = 1
    _fields_ = [
        ("length", c.c_ubyte),
        ("type", c.c_ubyte),
        ("number", c.c_ubyte),
        ("alternate", c.c_ubyte),
        ("endpoints", c.c_ubyte),
        ("class_", c.c_ubyte),
        ("subclass", c.c_ubyte),
        ("protocol", c.c_ubyte),
        ("index", c.c_ubyte),
    ]


class Pipe(c.Structure):
    _fields_ = [
        ("type", c.c_int),
        ("id", c.c_ubyte),
        ("maximum", c.c_ushort),
        ("interval", c.c_ubyte),
    ]


def bind(library, name, args, result=w.BOOL):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


def paths():
    """Enumerate active interfaces, including firmware-advertised interface GUIDs."""
    import winreg

    guids = {"A532D1A2-13A7-4099-94BE-C74535E35EAF", "A5DCBF10-6530-11D2-901F-00C04FB951ED"}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as usb:
        for index in range(winreg.QueryInfoKey(usb)[0]):
            device = winreg.EnumKey(usb, index)
            if not device.upper().startswith(("VID_0451&PID_E022", "VID_0451&PID_E012")):
                continue
            with winreg.OpenKey(usb, device) as instances:
                for instance in range(winreg.QueryInfoKey(instances)[0]):
                    name = winreg.EnumKey(instances, instance)
                    try:
                        with winreg.OpenKey(instances, name + r"\Device Parameters") as key:
                            for property_ in ("DeviceInterfaceGUID", "DeviceInterfaceGUIDs"):
                                try:
                                    values = winreg.QueryValueEx(key, property_)[0]
                                    guids.update([values] if isinstance(values, str) else values)
                                except FileNotFoundError:
                                    pass
                    except FileNotFoundError:
                        pass
    setup = c.WinDLL("setupapi", use_last_error=True)
    get = bind(
        setup, "SetupDiGetClassDevsW", [c.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD], w.HANDLE
    )
    enum = bind(
        setup,
        "SetupDiEnumDeviceInterfaces",
        [w.HANDLE, c.c_void_p, c.POINTER(GUID), w.DWORD, c.POINTER(Interface)],
    )
    detail = bind(
        setup,
        "SetupDiGetDeviceInterfaceDetailW",
        [w.HANDLE, c.POINTER(Interface), c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p],
    )
    destroy = bind(setup, "SetupDiDestroyDeviceInfoList", [w.HANDLE])
    seen = set()
    for value in sorted(guids):
        guid = GUID(value)
        devices = get(c.byref(guid), None, None, 0x12)
        if devices == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            index = 0
            while True:
                interface = Interface()
                interface.size = c.sizeof(interface)
                if not enum(devices, None, c.byref(guid), index, c.byref(interface)):
                    if c.get_last_error() == 259:
                        break
                    raise c.WinError(c.get_last_error())
                index += 1
                required = w.DWORD()
                detail(devices, c.byref(interface), None, 0, c.byref(required), None)
                if c.get_last_error() != 122 or not 8 <= required.value <= 65536:
                    raise c.WinError(c.get_last_error())
                buffer = c.create_string_buffer(required.value)
                c.cast(buffer, c.POINTER(w.DWORD))[0] = 8 if c.sizeof(c.c_void_p) == 8 else 6
                if not detail(devices, c.byref(interface), buffer, required, None, None):
                    raise c.WinError(c.get_last_error())
                path = c.wstring_at(c.addressof(buffer) + 4)
                lower = path.lower()
                if lower not in seen and any(
                    identity in lower for identity in ("vid_0451&pid_e022", "vid_0451&pid_e012")
                ):
                    seen.add(lower)
                    yield path
        finally:
            destroy(devices)


class WinUSB:
    def __init__(self, path):
        self.file = None
        self.usb = c.c_void_p()
        kernel = c.WinDLL("kernel32", use_last_error=True)
        library = c.WinDLL("winusb", use_last_error=True)
        self.close_file = bind(kernel, "CloseHandle", [w.HANDLE])
        create = bind(
            kernel,
            "CreateFileW",
            [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        )
        initialize = bind(library, "WinUsb_Initialize", [w.HANDLE, c.POINTER(c.c_void_p)])
        self.free = bind(library, "WinUsb_Free", [c.c_void_p])
        query = bind(
            library, "WinUsb_QueryInterfaceSettings", [c.c_void_p, c.c_ubyte, c.POINTER(Descriptor)]
        )
        pipe = bind(
            library, "WinUsb_QueryPipe", [c.c_void_p, c.c_ubyte, c.c_ubyte, c.POINTER(Pipe)]
        )
        self.policy = bind(
            library, "WinUsb_SetPipePolicy", [c.c_void_p, c.c_ubyte, w.DWORD, w.ULONG, c.c_void_p]
        )
        args = [c.c_void_p, c.c_ubyte, c.c_void_p, w.ULONG, c.POINTER(w.ULONG), c.c_void_p]
        self.read_pipe = bind(library, "WinUsb_ReadPipe", args)
        self.write_pipe = bind(library, "WinUsb_WritePipe", args)
        self.file = create(path, 0xC0000000, 3, None, 3, 0x40000000, None)
        if self.file == c.c_void_p(-1).value:
            self.file = None
            raise c.WinError(c.get_last_error())
        try:
            self.check(initialize(self.file, c.byref(self.usb)))
            descriptor = Descriptor()
            self.check(query(self.usb, 0, c.byref(descriptor)))
            endpoints = []
            for index in range(descriptor.endpoints):
                info = Pipe()
                self.check(pipe(self.usb, 0, index, c.byref(info)))
                if info.type == 2:
                    endpoints.append(info.id)
            self.input = next(ep for ep in endpoints if ep & 0x80)
            self.output = next(ep for ep in endpoints if not ep & 0x80)
            # Nspire's USB receiver completes a message on a short packet. A
            # whole 512-byte (CX II) or 64-byte (CX) transfer needs a terminal
            # zero-length packet, otherwise it waits until the next write.
            terminate = c.c_ubyte(1)
            self.check(
                self.policy(self.usb, self.output, 1, c.sizeof(terminate), c.byref(terminate))
            )
            self.set_timeout(self.output, 1500)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(result, operation="transfer"):
        if not result:
            error = c.get_last_error()
            if error == 121:
                raise TimeoutError(f"Calculator USB {operation} timed out")
            raise c.WinError(error)

    def set_timeout(self, endpoint, timeout):
        value = w.ULONG(max(1, int(timeout)))
        self.check(self.policy(self.usb, endpoint, 3, c.sizeof(value), c.byref(value)))

    def read(self, maximum, timeout=1500):
        self.set_timeout(self.input, timeout)
        buffer, length = c.create_string_buffer(maximum), w.ULONG()
        self.check(
            self.read_pipe(self.usb, self.input, buffer, maximum, c.byref(length), None), "read"
        )
        if not length.value:
            raise IOError("Empty calculator USB transfer")
        return buffer.raw[: length.value]

    def write(self, data):
        buffer, length = c.create_string_buffer(data), w.ULONG()
        self.check(
            self.write_pipe(self.usb, self.output, buffer, len(data), c.byref(length), None),
            "write",
        )
        if length.value != len(data):
            raise IOError("Incomplete calculator USB write")

    def close(self):
        if self.usb:
            self.free(self.usb)
            self.usb = c.c_void_p()
        if self.file:
            self.close_file(self.file)
            self.file = None
