"""Own one signed-driver display; never select or reconfigure an unrelated monitor."""

import ctypes as c
from ctypes import wintypes as w
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PRESETS = {
    "1× — 320 × 240": (320, 240),
    "1.5× — 480 × 360": (480, 360),
    "2× — 640 × 480": (640, 480),
    "2.5× — 800 × 600": (800, 600),
    "3× — 960 × 720": (960, 720),
    "4× — 1280 × 960": (1280, 960),
}


def validate_size(size):
    if len(size) != 2 or any(type(value) is not int or not 120 <= value <= 4096 for value in size):
        raise ValueError("Desktop width and height must be integers between 120 and 4096.")
    return tuple(size)


class POINT(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class DisplayFields(c.Structure):
    _fields_ = [("position", POINT), ("orientation", w.DWORD), ("fixed_output", w.DWORD)]


class ModeUnion(c.Union):
    _fields_ = [("display", DisplayFields), ("printer", w.SHORT * 8)]


class DEVMODE(c.Structure):
    _fields_ = [
        ("device", w.WCHAR * 32),
        ("spec_version", w.WORD),
        ("driver_version", w.WORD),
        ("size", w.WORD),
        ("extra", w.WORD),
        ("fields", w.DWORD),
        ("u", ModeUnion),
        ("color", w.SHORT),
        ("duplex", w.SHORT),
        ("y_resolution", w.SHORT),
        ("tt_option", w.SHORT),
        ("collate", w.SHORT),
        ("form", w.WCHAR * 32),
        ("log_pixels", w.WORD),
        ("bits", w.DWORD),
        ("width", w.DWORD),
        ("height", w.DWORD),
        ("display_flags", w.DWORD),
        ("frequency", w.DWORD),
        ("icm_method", w.DWORD),
        ("icm_intent", w.DWORD),
        ("media_type", w.DWORD),
        ("dither_type", w.DWORD),
        ("reserved1", w.DWORD),
        ("reserved2", w.DWORD),
        ("panning_width", w.DWORD),
        ("panning_height", w.DWORD),
    ]


class DISPLAY_DEVICE(c.Structure):
    _fields_ = [
        ("size", w.DWORD),
        ("name", w.WCHAR * 32),
        ("description", w.WCHAR * 128),
        ("flags", w.DWORD),
        ("instance", w.WCHAR * 128),
        ("key", w.WCHAR * 128),
    ]


def config_folder():
    if os.name != "nt":
        raise OSError("The calculator virtual display requires Windows.")
    shell = c.WinDLL("shell32", use_last_error=True)
    f = shell.SHGetFolderPathW
    f.argtypes = [w.HWND, c.c_int, w.HANDLE, w.DWORD, w.LPWSTR]
    f.restype = c.c_long
    folder = c.create_unicode_buffer(260)
    if f(None, 35, None, 0, folder) < 0:
        raise OSError("Windows could not locate ProgramData.")
    return Path(folder.value) / "ND Screen Mirror/VirtualDisplay"


def installed():
    if os.name != "nt":
        return False
    try:
        read_owner()
        return True
    except (OSError, ValueError):
        return False


def assets_folder():
    return (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    ) / "drivers/virtual-display"


def install_driver(report, size=(320, 240)):
    size = validate_size(size)
    lease = VirtualDisplay(owner={"setup": True})
    lease._lock()
    try:
        _install_driver(report, size)
    finally:
        lease.close()


def _install_driver(report, size):
    """The only privileged operation; the helper validates pinned hashes/signatures."""
    if os.name != "nt":
        raise OSError("Virtual display setup requires Windows.")
    source = assets_folder()
    cache = Path(os.environ["LOCALAPPDATA"]) / "ND Screen Mirror/DisplaySetup"
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("ND-Display-Setup.exe", "MttVDD.inf", "MttVDD.dll", "mttvdd.cat"):
        if not (source / name).is_file():
            raise OSError("The package is missing " + name)
        shutil.copy2(source / name, cache / name)

    class Execute(c.Structure):
        _fields_ = [
            ("size", w.DWORD),
            ("mask", w.ULONG),
            ("window", w.HWND),
            ("verb", w.LPCWSTR),
            ("file", w.LPCWSTR),
            ("parameters", w.LPCWSTR),
            ("directory", w.LPCWSTR),
            ("show", c.c_int),
            ("instance", w.HINSTANCE),
            ("id_list", c.c_void_p),
            ("class_name", w.LPCWSTR),
            ("class_key", w.HKEY),
            ("hotkey", w.DWORD),
            ("icon", w.HANDLE),
            ("process", w.HANDLE),
        ]

    shell = c.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteExW
    execute.argtypes = [c.POINTER(Execute)]
    execute.restype = w.BOOL
    kernel = c.WinDLL("kernel32", use_last_error=True)
    system_dir = c.create_unicode_buffer(32768)
    get_system = kernel.GetSystemDirectoryW
    get_system.argtypes = [w.LPWSTR, w.UINT]
    get_system.restype = w.UINT
    length = get_system(system_dir, len(system_dir))
    if not length or length >= len(system_dir):
        raise c.WinError(c.get_last_error())

    # Some Windows installations reject direct RunAs of this helper with error 3,
    # even when it runs correctly elevated. Use the system PowerShell launcher;
    # only this packaged helper performs installation, with its own hash checks.
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "& "
        + quote(cache / "ND-Display-Setup.exe")
        + " "
        + quote(cache)
        + " "
        + str(size[0])
        + " "
        + str(size[1])
        + "; exit $LASTEXITCODE"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    request = Execute()
    request.size = c.sizeof(request)
    request.mask = 0x40 | 0x400
    request.verb = "runas"
    request.file = str(Path(system_dir.value) / "WindowsPowerShell/v1.0/powershell.exe")
    request.parameters = subprocess.list2cmdline(
        ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    )
    request.directory = str(cache)
    request.show = 0
    report(
        "Windows needs administrator approval to register display resolutions. No sign-out is needed."
    )
    set_dll = kernel.SetDllDirectoryW
    set_dll.argtypes = [w.LPCWSTR]
    set_dll.restype = w.BOOL
    get_dll = kernel.GetDllDirectoryW
    get_dll.argtypes = [w.DWORD, w.LPWSTR]
    get_dll.restype = w.DWORD
    previous = c.create_unicode_buffer(32768)
    previous_length = get_dll(len(previous), previous)
    if previous_length >= len(previous):
        raise OSError("The Windows DLL search directory is too long.")
    if not set_dll(None):
        raise c.WinError(c.get_last_error())
    try:
        launched = execute(c.byref(request))
        launch_error = c.get_last_error()
    finally:
        set_dll(previous.value if previous_length else None)
    if not launched:
        error = launch_error
        if error == 1223:
            raise OSError("Display setup was cancelled.")
        raise c.WinError(error)
    wait = kernel.WaitForSingleObject
    wait.argtypes = [w.HANDLE, w.DWORD]
    wait.restype = w.DWORD
    exit_code = kernel.GetExitCodeProcess
    exit_code.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    exit_code.restype = w.BOOL
    close = kernel.CloseHandle
    close.argtypes = [w.HANDLE]
    close.restype = w.BOOL
    try:
        while wait(request.process, 200) == 258:
            pass
        result = w.DWORD()
        if not exit_code(request.process, c.byref(result)):
            raise c.WinError(c.get_last_error())
        if result.value == 2:
            raise OSError("Windows requires a restart to finish display driver setup.")
        if result.value != 0:
            raise OSError("Display driver setup failed. See the installer message.")
    finally:
        close(request.process)
    display = WindowsDisplays()
    deadline = time.monotonic() + 15
    while True:
        try:
            owner = read_owner()
            devices = [d for d in display.devices() if owned_device(d, owner)]
        except OSError:
            devices = []
        if devices and any(
            (m.width, m.height, m.frequency) == (*size, 60)
            for m in display.modes(devices[0]["name"])
        ):
            break
        if time.monotonic() > deadline:
            raise OSError(
                "The display driver was installed but Windows has not exposed its monitor."
            )
        time.sleep(0.15)
    display.detach(devices[0]["name"])
    report("Display resolutions are ready. Start creates the selected desktop.")


def read_owner():
    import winreg

    try:
        owner = json.loads((config_folder() / "owner.json").read_text())
    except (OSError, ValueError) as error:
        raise OSError("Set up the calculator display driver first.") from error
    if (
        owner.get("schema") != 1
        or owner.get("provider") != "VirtualDrivers/MttVDD"
        or not owner.get("device_instance")
    ):
        raise OSError("Calculator display ownership information is invalid.")
    root = "SYSTEM\\CurrentControlSet\\Enum\\" + owner["device_instance"]
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
        hardware = winreg.QueryValueEx(key, "HardwareID")[0]
    if not any(value.casefold() in ("root\\mttvdd", "mttvdd") for value in hardware):
        raise OSError("The owned display is not the expected virtual driver.")
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\MikeTheTech\\VirtualDisplayDriver"
    ) as key:
        configured = winreg.QueryValueEx(key, "VDDPATH")[0]
    if Path(configured).resolve() != config_folder().resolve():
        raise OSError("The virtual display driver is configured by another application.")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root + "\\Device Parameters") as key:
        owner["video_id"] = winreg.QueryValueEx(key, "VideoID")[0]
    return owner


def owned_device(device, owner):
    return ("\\control\\video\\" + owner["video_id"].casefold() + "\\") in device["key"].casefold()


def resolution_available(size):
    size = validate_size(size)
    try:
        api = WindowsDisplays()
        owner = read_owner()
        devices = [d for d in api.devices() if owned_device(d, owner)]
        return len(devices) == 1 and any(
            (m.width, m.height, m.frequency) == (*size, 60) for m in api.modes(devices[0]["name"])
        )
    except OSError:
        pass
    return False


def ensure_resolution(size, report):
    size = validate_size(size)
    if resolution_available(size):
        return
    report(f"Registering the {size[0]} × {size[1]} desktop resolution...")
    install_driver(report, size)


class WindowsDisplays:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows display APIs are unavailable.")
        self.user = c.WinDLL("user32", use_last_error=True)
        self.enum = self.user.EnumDisplayDevicesW
        self.enum.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(DISPLAY_DEVICE), w.DWORD]
        self.enum.restype = w.BOOL
        self.settings = self.user.EnumDisplaySettingsW
        self.settings.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(DEVMODE)]
        self.settings.restype = w.BOOL
        self.change = self.user.ChangeDisplaySettingsExW
        self.change.argtypes = [w.LPCWSTR, c.POINTER(DEVMODE), w.HWND, w.DWORD, c.c_void_p]
        self.change.restype = w.LONG

    def mode(self, name, number=0xFFFFFFFF):
        mode = DEVMODE()
        mode.size = c.sizeof(mode)
        return mode if self.settings(name, number, c.byref(mode)) else None

    def devices(self):
        result = []
        index = 0
        while True:
            d = DISPLAY_DEVICE()
            d.size = c.sizeof(d)
            if not self.enum(None, index, c.byref(d), 0):
                break
            result.append(
                dict(
                    name=d.name,
                    description=d.description,
                    instance=d.instance,
                    key=d.key,
                    flags=d.flags,
                    mode=self.mode(d.name),
                )
            )
            index += 1
        return result

    def modes(self, name):
        index = 0
        while True:
            mode = self.mode(name, index)
            if mode is None:
                break
            yield mode
            index += 1

    def saved_mode(self, name):
        return self.mode(name, 0xFFFFFFFE)

    def apply(self, name, mode):
        # An inactive IDD output must be staged before Windows rebuilds the
        # desktop topology. Immediate per-output changes can fail with -1.
        result = self.change(name, c.byref(mode), None, 0x10000001, None)
        if result == 0:
            result = self.change(None, None, None, 0, None)
        if result != 0:
            raise OSError(f"Windows rejected the calculator display setting (code {result}).")

    def detach(self, name):
        mode = self.mode(name) or self.mode(name, 0xFFFFFFFE) or DEVMODE()
        mode.size = c.sizeof(mode)
        mode.fields = 0x20 | 0x80000 | 0x100000
        mode.width = mode.height = 0
        self.apply(name, mode)


class VirtualDisplay:
    def __init__(self, api=None, owner=None, size=(320, 240)):
        self.size = validate_size(size)
        self.api = api or WindowsDisplays()
        self.owner = owner or read_owner()
        self.name = None
        self.region = None
        self.mutex = None

    def _lock(self):
        if self.mutex:
            return
        if os.name != "nt":
            return
        kernel = c.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateMutexW
        create.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
        create.restype = w.HANDLE
        wait = kernel.WaitForSingleObject
        wait.argtypes = [w.HANDLE, w.DWORD]
        wait.restype = w.DWORD
        self.close_handle = kernel.CloseHandle
        self.close_handle.argtypes = [w.HANDLE]
        self.close_handle.restype = w.BOOL
        self.release = kernel.ReleaseMutex
        self.release.argtypes = [w.HANDLE]
        self.release.restype = w.BOOL
        self.mutex = create(None, False, "Local\\ND-Screen-Mirror-Calculator-Display")
        if not self.mutex:
            raise c.WinError(c.get_last_error())
        if wait(self.mutex, 0) not in (0, 128):
            self.close_handle(self.mutex)
            self.mutex = None
            raise OSError("Another mirror app is using the calculator display.")

    def open(self):
        self._lock()
        try:
            devices = self.api.devices()
            owned = [d for d in devices if owned_device(d, self.owner)]
            if len(owned) != 1:
                raise OSError("Windows could not identify the owned calculator display.")
            real = [
                d for d in devices if d not in owned and d["flags"] & 1 and d["mode"] is not None
            ]
            if not real:
                raise OSError(
                    "Keep another Windows display connected while using the calculator display."
                )
            if owned[0]["flags"] & 4:
                raise OSError(
                    "The calculator display is marked primary. Make a regular monitor primary first."
                )
            mode = next(
                (
                    m
                    for m in self.api.modes(owned[0]["name"])
                    if (m.width, m.height, m.frequency) == (*self.size, 60)
                ),
                None,
            )
            if mode is None:
                raise OSError(
                    f"The display driver does not offer the required {self.size[0]}×{self.size[1]} mode."
                )
            previous = owned[0]["mode"]
            if previous is None and hasattr(self.api, "saved_mode"):
                previous = self.api.saved_mode(owned[0]["name"])
            x, y = display_position(self.size, previous, [d["mode"] for d in real])
            mode.u.display.position.x = x
            mode.u.display.position.y = y
            mode.fields = 0x20 | 0x40000 | 0x80000 | 0x100000 | 0x400000
            self.name = owned[0]["name"]
            self.api.apply(self.name, mode)
            actual = self.api.mode(self.name)
            if actual is None or (actual.width, actual.height) != self.size:
                raise OSError("Windows did not activate the exact requested desktop size.")
            self.region = dict(
                left=actual.u.display.position.x,
                top=actual.u.display.position.y,
                width=actual.width,
                height=actual.height,
            )
            after = self.api.devices()

            def fingerprint(d):
                m = d["mode"]
                return (
                    (
                        d["name"],
                        d["flags"] & 5,
                        m.width,
                        m.height,
                        m.frequency,
                        m.bits,
                        m.u.display.position.x,
                        m.u.display.position.y,
                    )
                    if m is not None
                    else None
                )

            expected = {d["name"]: fingerprint(d) for d in real}
            observed = {d["name"]: fingerprint(d) for d in after if d["name"] in expected}
            if observed != expected:
                raise OSError(
                    "Windows unexpectedly changed an existing display; the calculator display was detached."
                )
            return self
        except BaseException:
            self.close()
            raise

    def refresh_region(self):
        if not self.name:
            raise OSError("The virtual display is not attached.")
        mode = self.api.mode(self.name)
        if mode is None or not mode.width or not mode.height:
            raise OSError("The virtual display was disconnected in Windows.")
        self.region.update(
            left=mode.u.display.position.x,
            top=mode.u.display.position.y,
            width=mode.width,
            height=mode.height,
        )
        return self.region

    def close(self):
        try:
            if self.name:
                self.api.detach(self.name)
                self.name = None
        finally:
            if self.mutex:
                self.release(self.mutex)
                self.close_handle(self.mutex)
                self.mutex = None


def display_position(size, previous, physical):
    """Keep an existing placement and its touching edge when resizing."""
    width, height = size
    if previous is not None:
        x, y = previous.u.display.position.x, previous.u.display.position.y
        for real in physical:
            rx, ry = real.u.display.position.x, real.u.display.position.y
            side = x + previous.width == rx or x == rx + real.width
            above = y + previous.height == ry
            below = y == ry + real.height
            if side:
                if x + previous.width == rx:
                    x = rx - width
                if y != ry and y + previous.height == ry + real.height:
                    y = ry + real.height - height
                break
            if above or below:
                if above:
                    y = ry - height
                if x != rx and x + previous.width == rx + real.width:
                    x = rx + real.width - width
                break
        touches = overlaps = False
        for real in physical:
            rx, ry = real.u.display.position.x, real.u.display.position.y
            horizontal = min(x + width, rx + real.width) > max(x, rx)
            vertical = min(y + height, ry + real.height) > max(y, ry)
            overlaps |= horizontal and vertical
            touches |= ((x + width == rx or x == rx + real.width) and vertical) or (
                (y + height == ry or y == ry + real.height) and horizontal
            )
        if touches and not overlaps:
            return x, y
    anchor = max(physical, key=lambda mode: mode.u.display.position.x + mode.width)
    return anchor.u.display.position.x + anchor.width, anchor.u.display.position.y
