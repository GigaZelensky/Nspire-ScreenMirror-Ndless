"""Linux input and USB fault paths, using fake devices only (including on Windows)."""

from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from linux_input import LinuxInput, host_layout
from linux_usb import LinuxUSB, LibusbIO, open_device
from remote_input import RemoteInput
from wire import HELLO, acknowledgement, control

# Real ecodes on Linux; matching named capabilities are sufficient on Windows.
try:
    from evdev import ecodes
except ImportError:
    names = ["KEY_" + c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"] + [
        "KEY_LEFTSHIFT",
        "KEY_LEFTCTRL",
        "KEY_LEFTALT",
        "KEY_SPACE",
        "KEY_TAB",
        "KEY_ESC",
        "KEY_BACKSPACE",
        "KEY_ENTER",
        "KEY_DOT",
        "KEY_COMMA",
        "KEY_SEMICOLON",
        "KEY_KPMINUS",
        "KEY_KPPLUS",
        "KEY_KPASTERISK",
        "KEY_KPSLASH",
        "KEY_UP",
        "KEY_DOWN",
        "KEY_LEFT",
        "KEY_RIGHT",
        "KEY_HOME",
        "BTN_LEFT",
        "BTN_RIGHT",
        "BTN_MIDDLE",
        "REL_X",
        "REL_Y",
        "REL_WHEEL",
        "REL_WHEEL_HI_RES",
        "EV_KEY",
        "EV_REL",
    ]
    ecodes = SimpleNamespace(**{name: index + 1 for index, name in enumerate(names)})

devices = []


class InputDevice:
    def __init__(self, events, name):
        self.events, self.name = events, name
        self.writes = []
        self.closed = False
        devices.append(self)

    def write(self, *args):
        assert args[1] in self.events[args[0]]
        self.writes.append(args)

    def syn(self):
        self.writes.append(("SYN",))

    def close(self):
        self.closed = True


module = SimpleNamespace(ecodes=ecodes, UInput=InputDevice)
backend = LinuxInput(module=module)
keyboard, mouse = devices
remote = RemoteInput(dict(left=0, top=0, width=640, height=480), backend=backend)
remote.apply(
    [
        (1, 3, 1, ord("W"), 0, 0),
        (2, 3, 3, ord("W"), 0, 0),
        (3, 3, 0, ord("W"), 0, 0),
        (4, 3, 1, 0x12, 0, 0),
        (5, 3, 1, 0x09, 0, 0),
        (6, 2, 1, 1, 0, 0),
    ]
)
assert keyboard.writes[:6] == [
    (ecodes.EV_KEY, ecodes.KEY_W, 1),
    ("SYN",),
    (ecodes.EV_KEY, ecodes.KEY_W, 2),
    ("SYN",),
    (ecodes.EV_KEY, ecodes.KEY_W, 0),
    ("SYN",),
]
backend.move(-12, 7, remote.region)
assert mouse.writes[-3:] == [
    (ecodes.EV_REL, ecodes.REL_X, -12),
    (ecodes.EV_REL, ecodes.REL_Y, 7),
    ("SYN",),
]
backend.wheel(40)
backend.wheel(40)
backend.wheel(40)
assert sum(e[2] for e in mouse.writes if len(e) == 3 and e[1] == ecodes.REL_WHEEL) == 1
backend.wheel(-240)
assert sum(e[2] for e in mouse.writes if len(e) == 3 and e[1] == ecodes.REL_WHEEL) == -1
remote.close()
assert all(d.closed for d in devices) and not backend.held and not backend.buttons
assert keyboard.writes[-4:] == [
    (ecodes.EV_KEY, ecodes.KEY_TAB, 0),
    ("SYN",),
    (ecodes.EV_KEY, ecodes.KEY_LEFTALT, 0),
    ("SYN",),
]
before = len(devices)
try:
    LinuxInput(confine=True, module=module)
    raise AssertionError("Unsupported confinement was ignored")
except ValueError:
    pass
assert len(devices) == before

# Linux interprets evdev positions through the desktop layout. Under AZERTY,
# the US M position is comma; calculator M must use the semicolon position.
with patch.dict("os.environ", {"XKB_DEFAULT_LAYOUT": "fr"}):
    assert host_layout("auto") == "fr"
    assert host_layout("us") == "us"
    french = LinuxInput(module=module)
    french_remote = RemoteInput(remote.region, backend=french)
    keys = [
        (ord("A"), ecodes.KEY_Q),
        (ord("Q"), ecodes.KEY_A),
        (ord("W"), ecodes.KEY_Z),
        (ord("Z"), ecodes.KEY_W),
        (ord("M"), ecodes.KEY_SEMICOLON),
        (ord("B"), ecodes.KEY_B),
    ]
    sequence = 1
    for letter, expected in keys:
        french_remote.apply(
            [
                (sequence, 3, 1, 0x11, 0, 0),
                (sequence + 1, 3, 1, letter, 0, 0),
                (sequence + 2, 3, 3, letter, 0, 0),
            ]
        )
        assert french.keyboard.writes[-4:] == [
            (ecodes.EV_KEY, expected, 1),
            ("SYN",),
            (ecodes.EV_KEY, expected, 2),
            ("SYN",),
        ]
        french_remote.release()
        assert french.keyboard.writes[-4:] == [
            (ecodes.EV_KEY, expected, 0),
            ("SYN",),
            (ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0),
            ("SYN",),
        ]
        sequence += 3
    french_remote.close()
for unsupported in ("de", "us,fr", ""):
    with patch.dict("os.environ", {"XKB_DEFAULT_LAYOUT": unsupported, "LANG": "fr_FR.UTF-8"}):
        assert host_layout("auto") == "us"
try:
    LinuxInput(module=module, keyboard_layout="invalid")
    raise AssertionError("Invalid keyboard layout was accepted")
except ValueError:
    pass


def partial_failure(events, name):
    if "mouse" in name:
        raise PermissionError("test uinput permission")
    return InputDevice(events, name)


try:
    LinuxInput(module=SimpleNamespace(ecodes=ecodes, UInput=partial_failure))
    raise AssertionError("Missing permissions were ignored")
except OSError as error:
    assert "/dev/uinput" in str(error) and devices[-1].closed


class USBError(Exception):
    pass


class Timeout(USBError):
    pass


usb = SimpleNamespace(USBError=USBError, USBErrorTimeout=Timeout)


class Handle:
    closed = False

    def __init__(self):
        self.writes = []
        self.released = []

    def bulkRead(self, endpoint, maximum, timeout):
        assert endpoint == 0x81 and timeout >= 1
        return b"reply"

    def bulkWrite(self, endpoint, data, timeout):
        assert endpoint == 1 and timeout == 1500
        self.writes.append(bytes(data))
        return len(data)

    def releaseInterface(self, interface):
        self.released.append(interface)

    def close(self):
        self.closed = True

    def getConfiguration(self):
        return 1

    def setAutoDetachKernelDriver(self, enabled):
        assert enabled is True

    def claimInterface(self, number):
        self.claimed = number


handle = Handle()
io = LibusbIO(handle, 2, 0x81, 1, 64, usb)
for size in (1, 63, 64, 65, 512, 1024):
    handle.writes.clear()
    io.write(b"x" * size)
    assert handle.writes == [b"x" * size] + ([b""] if size % 64 == 0 else [])
assert io.read(2048, 0.5) == b"reply"
io.close()
io.close()
assert handle.closed and handle.released == [2]


class Endpoint:
    def __init__(self, address):
        self.address = address

    def getAddress(self):
        return self.address

    def getAttributes(self):
        return 2

    def getMaxPacketSize(self):
        return 64


class Setting:
    def getAlternateSetting(self):
        return 0

    def iterEndpoints(self):
        return iter([Endpoint(0x81), Endpoint(1)])

    def getNumber(self):
        return 2


class Configuration:
    def getConfigurationValue(self):
        return 1

    def iterInterfaces(self):
        return [SimpleNamespace(iterSettings=lambda: [Setting()])]


class Device:
    def __init__(self, vendor=0x0451, product=0xE022):
        self.vendor, self.product = vendor, product
        self.handle = Handle()

    def getVendorID(self):
        return self.vendor

    def getProductID(self):
        return self.product

    def open(self):
        return self.handle

    def iterConfigurations(self):
        return iter([Configuration()])


device = Device()
io = open_device(device, usb)
assert device.handle.claimed == 2
io.close()


class Context:
    def __init__(self):
        self.devices = [Device(123), Device(product=123), Device()]
        self.closed = False

    def getDeviceList(self, skip_on_error):
        assert skip_on_error
        return self.devices

    def close(self):
        self.closed = True


usb.USBContext = Context
opened = []


class Protocol:
    def __init__(self, io, cx2):
        self.io, self.cx2 = io, cx2
        self.connected = False
        opened.append(self)

    def begin(self):
        self.connected = True
        return True

    def end(self):
        self.connected = False


with patch("linux_usb.NspireProtocol", Protocol):
    connection = LinuxUSB(module=usb)
    assert connection.begin() and len(opened) == 1 and opened[0].cx2
    connection.end()
    assert connection.protocol is opened[0] and opened[0].io.handle is not None
    assert connection.begin() and len(opened) == 1
    connection.end()
    connection.close()
    assert connection.protocol is None and opened[0].io.handle is None
    assert connection.context.closed
    connection = LinuxUSB(module=usb)
    connection.context.devices.clear()
    assert connection.begin() is False
    connection.close()

# Full shared wire framing over fake bulk I/O, including per-control reopen.
from usb_protocol import Device as ProtocolDevice

protocol_device = ProtocolDevice(True)
handle = Handle()
handle.bulkRead = lambda endpoint, maximum, timeout: protocol_device.read(maximum, timeout)
handle.bulkWrite = lambda endpoint, data, timeout: (protocol_device.write(data), len(data))[1]
from nspire_usb import NspireProtocol

connection = NspireProtocol(LibusbIO(handle, 0, 0x81, 1, 512, usb))
assert connection.begin()
assert acknowledgement(connection.exchange(control(HELLO, 123)), 123)["status"] == 0
connection.end()
print(
    "PASS Linux key chords/repeats/release, relative mouse/scroll, permissions, USB discovery, ZLP and wire interop"
)
if sys.platform == "linux":
    import usb1

    assert all(hasattr(usb1.USBDevice, name) for name in ("iterConfigurations", "open"))
    assert all(
        hasattr(usb1.USBConfiguration, name) for name in ("getConfigurationValue", "iterInterfaces")
    )
    assert all(
        hasattr(usb1.USBInterfaceSetting, name)
        for name in ("getNumber", "iterEndpoints", "getAlternateSetting")
    )
    subprocess.run(
        ["xvfb-run", "-a", sys.executable, str(Path(__file__).with_name("linux_gui.py"))],
        check=True,
        timeout=15,
    )
