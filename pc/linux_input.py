"""Relative keyboard/mouse injection through Linux uinput (X11 and Wayland)."""

import math
import os


def host_layout(layout):
    if layout not in ("auto", "us", "fr"):
        raise ValueError("Linux keyboard layout must be auto, us or fr")
    if layout == "auto":
        # This is an xkbcommon default, not the process locale. Do not infer
        # keyboard layout from the user's language or guess compositor APIs.
        configured = os.environ.get("XKB_DEFAULT_LAYOUT", "")
        return configured if configured in ("us", "fr") else "us"
    return layout


def key_codes(ecodes, layout="us"):
    names = {ord(letter): "KEY_" + letter for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
    if layout == "fr":
        names.update(
            {
                ord("A"): "KEY_Q",
                ord("Q"): "KEY_A",
                ord("W"): "KEY_Z",
                ord("Z"): "KEY_W",
                ord("M"): "KEY_SEMICOLON",
            }
        )
    names.update(
        {
            0x10: "KEY_LEFTSHIFT",
            0x11: "KEY_LEFTCTRL",
            0x12: "KEY_LEFTALT",
            0x20: "KEY_SPACE",
            0x09: "KEY_TAB",
            0x1B: "KEY_ESC",
            0x08: "KEY_BACKSPACE",
            0x0D: "KEY_ENTER",
            0xBE: "KEY_DOT",
            0xBC: "KEY_COMMA",
            0x6D: "KEY_KPMINUS",
            0x6B: "KEY_KPPLUS",
            0x6A: "KEY_KPASTERISK",
            0x6F: "KEY_KPSLASH",
            0x26: "KEY_UP",
            0x28: "KEY_DOWN",
            0x25: "KEY_LEFT",
            0x27: "KEY_RIGHT",
            0x24: "KEY_HOME",
        }
    )
    return {code: getattr(ecodes, name) for code, name in names.items()}


class LinuxInput:
    def __init__(self, confine=False, module=None, keyboard_layout="auto"):
        if confine:
            raise ValueError(
                "Pointer confinement is only available on Windows; turn it off on Linux"
            )
        self.layout = host_layout(keyboard_layout)
        if module is None:
            import evdev as module
        self.e = module.ecodes
        self.codes = key_codes(self.e, self.layout)
        self.held = set()
        self.buttons = set()
        self.scroll = 0
        self.keyboard = self.mouse = None
        try:
            self.keyboard = module.UInput(
                {self.e.EV_KEY: sorted(set(self.codes.values()))}, name="ND Screen Mirror keyboard"
            )
            self.mouse = module.UInput(
                {
                    self.e.EV_KEY: [self.e.BTN_LEFT, self.e.BTN_RIGHT, self.e.BTN_MIDDLE],
                    self.e.EV_REL: [
                        self.e.REL_X,
                        self.e.REL_Y,
                        self.e.REL_WHEEL,
                        self.e.REL_WHEEL_HI_RES,
                    ],
                },
                name="ND Screen Mirror mouse",
            )
        except OSError as error:
            self.close()
            raise OSError(
                "Cannot create Linux input devices. Follow docs/linux.md to enable /dev/uinput, "
                "or use --no-input for viewing only."
            ) from error

    def key(self, code, down):
        if code not in self.codes:
            raise ValueError(f"Unsupported calculator key 0x{code:02X}")
        self.keyboard.write(
            self.e.EV_KEY, self.codes[code], (2 if code in self.held else 1) if down else 0
        )
        self.keyboard.syn()
        if down:
            self.held.add(code)
        else:
            self.held.discard(code)

    def button(self, code, down):
        button = {1: self.e.BTN_LEFT, 2: self.e.BTN_RIGHT, 3: self.e.BTN_MIDDLE}[code]
        self.mouse.write(self.e.EV_KEY, button, int(down))
        self.mouse.syn()
        if down:
            self.buttons.add(code)
        else:
            self.buttons.discard(code)

    def move(self, dx, dy, region):
        if dx:
            self.mouse.write(self.e.EV_REL, self.e.REL_X, dx)
        if dy:
            self.mouse.write(self.e.EV_REL, self.e.REL_Y, dy)
        self.mouse.syn()

    def wheel(self, amount):
        self.mouse.write(self.e.EV_REL, self.e.REL_WHEEL_HI_RES, amount)
        self.scroll += amount
        steps = math.trunc(self.scroll / 120)
        if steps:
            self.mouse.write(self.e.EV_REL, self.e.REL_WHEEL, steps)
            self.scroll -= steps * 120
        self.mouse.syn()

    def close(self):
        first_error = None
        for name in ("keyboard", "mouse"):
            device = getattr(self, name, None)
            if device is not None:
                try:
                    device.close()
                except OSError as error:
                    first_error = first_error or error
                finally:
                    setattr(self, name, None)
        if first_error:
            raise first_error
