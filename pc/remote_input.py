"""Apply acknowledged calculator input only while the user is mirroring."""

import ctypes as c
import os
import math
import time
import sys


class PointerMotion:
    """Small-motion hysteresis and adaptive interpolation, in desktop pixels."""

    def __init__(self, backend, region, clock=time.monotonic):
        self.backend = backend
        self.region = region
        self.clock = clock
        self.reset()

    def reset(self):
        self.target = [0.0, 0.0]
        self.filtered = [0.0, 0.0]
        self.emitted = [0, 0]
        self.last = self.clock()
        self.pending = False
        self.fast = False

    def feed(self, x, y):
        # The player's dominant-axis guard removes wobble on straight swipes.
        if abs(x) >= 2 * abs(y):
            y = 0
        elif abs(y) >= 2 * abs(x):
            x = 0
        self.target[0] += x
        self.target[1] += y
        self.fast = math.hypot(x * 320 / self.region["width"], y * 240 / self.region["height"]) >= 2
        self.pending = True
        self.tick()

    def tick(self, settle=False):
        if not self.pending:
            return
        now = self.clock()
        dt = max(0.005, min(0.05, now - self.last))
        self.last = now
        alpha = (
            1.0
            if settle
            else max(
                0.85 if self.fast else 0.0, 1 - math.exp(-dt / (0.004 if self.fast else 0.012))
            )
        )
        for i in range(2):
            self.filtered[i] += alpha * (self.target[i] - self.filtered[i])
        wanted = [round(v) for v in self.filtered]
        delta = [wanted[i] - self.emitted[i] for i in range(2)]
        # Roughly a quarter of a calculator pixel, independently of PC resolution.
        threshold = [max(1, self.region["width"] / 1280), max(1, self.region["height"] / 960)]
        if settle or any(abs(self.target[i] - self.emitted[i]) >= threshold[i] for i in range(2)):
            if delta[0] or delta[1]:
                self.backend.move(*delta, self.region)
                self.emitted = wanted
        if settle or all(abs(self.target[i] - self.filtered[i]) < 0.05 for i in range(2)):
            self.pending = False

    def freeze(self):
        # Clicking must not continue a filtered tail underneath the button press.
        threshold = [max(1, self.region["width"] / 1280), max(1, self.region["height"] / 960)]
        if any(abs(self.target[i] - self.emitted[i]) >= threshold[i] for i in range(2)):
            self.tick(settle=True)
        self.target = list(map(float, self.emitted))
        self.filtered = self.target.copy()
        self.pending = False


class WindowsInput:
    def __init__(self, confine=False):
        self.confine = confine
        from ctypes import wintypes as w

        class Mouse(c.Structure):
            _fields_ = [
                ("dx", w.LONG),
                ("dy", w.LONG),
                ("data", w.DWORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", c.c_size_t),
            ]

        class Keyboard(c.Structure):
            _fields_ = [
                ("key", w.WORD),
                ("scan", w.WORD),
                ("flags", w.DWORD),
                ("time", w.DWORD),
                ("extra", c.c_size_t),
            ]

        class Hardware(c.Structure):
            _fields_ = [("message", w.DWORD), ("low", w.WORD), ("high", w.WORD)]

        class Union(c.Union):
            _fields_ = [("mouse", Mouse), ("keyboard", Keyboard), ("hardware", Hardware)]

        class Input(c.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", w.DWORD), ("value", Union)]

        self.Mouse, self.Keyboard, self.Input = Mouse, Keyboard, Input
        self.user = c.WinDLL("user32", use_last_error=True)
        self.send = self.user.SendInput
        self.send.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
        self.send.restype = w.UINT
        self.position = self.user.GetCursorPos
        self.position.argtypes = [c.POINTER(w.POINT)]
        self.position.restype = w.BOOL
        self.metric = self.user.GetSystemMetrics
        self.metric.argtypes = [c.c_int]
        self.metric.restype = c.c_int
        self.Point = w.POINT
        self.foreground = self.user.GetForegroundWindow
        self.foreground.argtypes = []
        self.foreground.restype = w.HWND
        self.window_thread = self.user.GetWindowThreadProcessId
        self.window_thread.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.window_thread.restype = w.DWORD
        self.layout = self.user.GetKeyboardLayout
        self.layout.argtypes = [w.DWORD]
        self.layout.restype = w.HANDLE
        self.map_key = self.user.MapVirtualKeyExW
        self.map_key.argtypes = [w.UINT, w.UINT, w.HANDLE]
        self.map_key.restype = w.UINT
        self.held_scans = {}

    def emit(self, value):
        if self.send(1, c.byref(value), c.sizeof(value)) != 1:
            raise OSError(
                "Windows blocked remote input. Switch away from an elevated app or secure desktop."
            )

    def key(self, code, down):
        # Games may read physical scan codes instead of window key messages.
        # Keep the original scan through repeats/release if the layout changes.
        scan = self.held_scans.get(code)
        if scan is None:
            thread = self.window_thread(self.foreground(), None)
            scan = self.map_key(code, 4, self.layout(thread))  # MAPVK_VK_TO_VSC_EX
            if not scan or scan >> 8 not in (0, 0xE0):
                raise OSError(f"Windows cannot map calculator key 0x{code:02X} to a scan code")
        flags = 8 | (0 if down else 2) | (1 if scan >> 8 == 0xE0 else 0)
        value = self.Input()
        value.type = 1
        value.keyboard = self.Keyboard(0, scan & 0xFF, flags, 0, 0)
        self.emit(value)
        if down:
            self.held_scans[code] = scan
        else:
            self.held_scans.pop(code, None)

    def button(self, code, down):
        flags = {1: (2, 4), 2: (8, 16), 3: (32, 64)}[code][not down]
        value = self.Input()
        value.type = 0
        value.mouse = self.Mouse(0, 0, 0, flags, 0, 0)
        self.emit(value)

    def wheel(self, amount):
        value = self.Input()
        value.type = 0
        value.mouse = self.Mouse(0, 0, amount & 0xFFFFFFFF, 0x800, 0, 0)
        self.emit(value)

    def move(self, dx, dy, region):
        if not self.confine:
            # Absolute cursor warps produce large camera jumps in mouselook games.
            value = self.Input()
            value.type = 0
            value.mouse = self.Mouse(dx, dy, 0, 1, 0, 0)  # MOUSEEVENTF_MOVE
            self.emit(value)
            return
        point = self.Point()
        if not self.position(c.byref(point)):
            raise c.WinError(c.get_last_error())
        vl, vt, vw, vh = (self.metric(i) for i in (76, 77, 78, 79))
        if vw < 2 or vh < 2:
            raise OSError("Windows desktop dimensions are unavailable")
        left, top, width, height = vl, vt, vw, vh
        if self.confine:
            left, top, width, height = (region[key] for key in ("left", "top", "width", "height"))
            if not (left <= point.x < left + width and top <= point.y < top + height):
                point.x, point.y = left + width // 2, top + height // 2
        x = max(left, min(left + width - 1, point.x + dx))
        y = max(top, min(top + height - 1, point.y + dy))
        value = self.Input()
        value.type = 0
        value.mouse = self.Mouse(
            round((x - vl) * 65535 / (vw - 1)), round((y - vt) * 65535 / (vh - 1)), 0, 0xC001, 0, 0
        )
        self.emit(value)


class RemoteInput:
    def __init__(
        self,
        region,
        gain=1.0,
        backend=None,
        confine=False,
        reverse_scroll=False,
        scroll_gain=1.0,
        keyboard_layout="auto",
    ):
        if backend is None:
            if os.name == "nt":
                backend = WindowsInput(confine=confine)
            elif sys.platform == "linux":
                from linux_input import LinuxInput

                backend = LinuxInput(confine=confine, keyboard_layout=keyboard_layout)
            else:
                raise OSError("Calculator control requires Windows or Linux; use --no-input")
        self.backend = backend
        self.region = region
        self.gain = gain
        self.scroll_gain = scroll_gain
        self.scroll_direction = 1 if reverse_scroll else -1
        self.ack = 0
        self.keys = set()
        self.buttons = set()
        self.scroll = 0.0
        self.motion = PointerMotion(backend, region)

    def apply(self, events):
        for sequence, kind, flags, code, x, y in events:
            if sequence <= self.ack:
                continue
            if kind == 1:
                self.motion.feed(
                    x * self.region["width"] * self.gain / 32767,
                    y * self.region["height"] * self.gain / 32767,
                )
            elif kind == 2:
                down = bool(flags & 1)
                if down != (code in self.buttons):
                    self.motion.freeze()
                    self.backend.button(code, down)
                    if down:
                        self.buttons.add(code)
                    else:
                        self.buttons.discard(code)
            elif kind == 3:
                down = bool(flags & 1)
                if down != (code in self.keys) or down and flags & 2:
                    self.backend.key(code, down)
                    if down:
                        self.keys.add(code)
                    else:
                        self.keys.discard(code)
            elif kind == 4:
                self.scroll += y * 120 * 12 * self.scroll_gain * self.scroll_direction / 32767
                amount = math.trunc(self.scroll)
                if amount:
                    self.backend.wheel(amount)
                    self.scroll -= amount
            elif kind == 5:
                self.release()
            self.ack = sequence

    def tick(self):
        self.motion.tick()

    def release(self):
        first_error = None
        # Release ordinary keys before modifiers, and attempt every release.
        for code in sorted(self.keys, key=lambda code: code in (0x10, 0x11, 0x12)):
            try:
                self.backend.key(code, False)
                self.keys.discard(code)
            except OSError as error:
                first_error = first_error or error
        for code in list(self.buttons):
            try:
                self.backend.button(code, False)
                self.buttons.discard(code)
            except OSError as error:
                first_error = first_error or error
        self.scroll = 0.0
        self.motion.reset()
        if first_error:
            raise first_error

    def close(self):
        try:
            self.release()
        finally:
            close = getattr(self.backend, "close", None)
            if close:
                close()
