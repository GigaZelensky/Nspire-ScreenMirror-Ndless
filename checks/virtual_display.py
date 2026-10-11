"""Exercise actual display lease/ABI with a fake display API; no monitor changes."""

from pathlib import Path
import sys
import ctypes as c

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from virtual_display import (
    VirtualDisplay,
    WindowsDisplays,
    DEVMODE,
    DISPLAY_DEVICE,
    validate_size,
    display_position,
)

assert c.sizeof(DEVMODE) == 220 and c.sizeof(DISPLAY_DEVICE) == 840


# Fake display tests must not reserve the real app's cross-process display lock.
class FakeDisplayLease(VirtualDisplay):
    def _lock(self):
        pass


VirtualDisplay = FakeDisplayLease


def mode(width, height, x=0, y=0):
    result = DEVMODE()
    result.size = c.sizeof(result)
    result.width = width
    result.height = height
    result.frequency = 60
    result.bits = 32
    result.u.display.position.x = x
    result.u.display.position.y = y
    return result


class API:
    def __init__(self):
        self.real = [
            dict(name="real1", instance="GPU", key="physical1", flags=5, mode=mode(2560, 1440)),
            dict(
                name="real2",
                instance="GPU",
                key="physical2",
                flags=1,
                mode=mode(1360, 768, -1360, 665),
            ),
        ]
        self.virtual = dict(
            name="owned",
            instance="Root\\MttVDD",
            key="\\Registry\\Machine\\System\\CurrentControlSet\\Control\\Video\\{OWN}\\0000",
            flags=0,
            mode=None,
        )
        self.calls = []
        self.accept = True
        self.wrong_size = False

    def devices(self):
        return [*self.real, self.virtual]

    def modes(self, name):
        return [mode(320, 240)] if self.accept else [mode(640, 480)]

    def apply(self, name, value):
        assert name == "owned", "An unrelated monitor was changed"
        self.calls.append((name, value.width, value.height))
        self.virtual["flags"] = 1
        self.virtual["mode"] = mode(
            640 if self.wrong_size else value.width,
            value.height,
            value.u.display.position.x,
            value.u.display.position.y,
        )

    def mode(self, name):
        return self.virtual["mode"]

    def detach(self, name):
        assert name == "owned"
        self.calls.append((name, 0, 0))
        self.virtual["flags"] = 0
        self.virtual["mode"] = None


owner = dict(device_instance="Root\\Display\\123", video_id="{OWN}")
api = API()
lease = VirtualDisplay(api, owner).open()
assert lease.region == dict(left=2560, top=0, width=320, height=240)
lease.close()
assert api.calls == [("owned", 320, 240), ("owned", 0, 0)]
assert api.real[0]["flags"] == 5 and api.real[1]["mode"].u.display.position.x == -1360
api = API()
lease = VirtualDisplay(api, owner).open()
shared = lease.region
api.virtual["mode"].u.display.position.y = 1200
assert lease.refresh_region() is shared and shared["top"] == 1200
lease.open()
assert lease.region["top"] == 1200, "Restart reset a user-arranged display"
lease.close()
primary = mode(2560, 1440)
assert display_position((320, 240), mode(320, 240, 2560, 1200), [primary]) == (2560, 1200)
assert display_position((480, 360), mode(640, 480, 2560, 960), [primary]) == (2560, 1080)
assert display_position((480, 360), mode(640, 480, 1920, 1440), [primary]) == (2080, 1440)
assert display_position((480, 360), mode(640, 480, -640, 0), [primary]) == (-480, 0)
assert display_position((480, 360), mode(640, 480, 0, -480), [primary]) == (0, -360)
api = API()
api.accept = False
try:
    VirtualDisplay(api, owner).open()
except OSError:
    pass
else:
    raise AssertionError("Unsupported mode was accepted")
assert not api.calls
api = API()
api.wrong_size = True
try:
    VirtualDisplay(api, owner).open()
except OSError:
    pass
else:
    raise AssertionError("Wrong resolution readback was accepted")
assert api.calls[-1] == ("owned", 0, 0)
api = API()
api.virtual["flags"] = 5
try:
    VirtualDisplay(api, owner).open()
except OSError:
    pass
else:
    raise AssertionError("A primary virtual display was altered")
assert not api.calls
for size in [(480, 360), (640, 480), (720, 405)]:
    api = API()
    api.modes = lambda name: [mode(*size)]
    lease = VirtualDisplay(api, owner, size=size).open()
    assert (lease.region["width"], lease.region["height"]) == size
    lease.close()
    assert api.calls == [("owned", *size), ("owned", 0, 0)]
for size in [(0, 240), (-320, 240), (4097, 240), (320, 119), (320.5, 240), (320,)]:
    try:
        validate_size(size)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid desktop dimensions accepted")
staged = WindowsDisplays.__new__(WindowsDisplays)
calls = []


def change(name, settings, window, flags, parameter):
    calls.append((name, settings is not None, flags))
    return 0


staged.change = change
staged.apply("owned", mode(320, 240))
assert calls == [("owned", True, 0x10000001), (None, False, 0)]
calls.clear()
staged.change = lambda *args: -1
try:
    staged.apply("owned", mode(320, 240))
except OSError:
    pass
else:
    raise AssertionError("Failed desktop topology staging was ignored")
print(
    "PASS Win32 structure sizes, exact ownership, 320x240 mode, right-hand placement, stop cleanup and failure rollback; no real monitor changes"
)
