"""Clock choices shared by the desktop UI, preferences and command line."""

CX_CLOCKS = tuple(range(132, 241, 12))
CX2_CLOCKS = tuple(range(396, 493, 12))
CLOCKS = (0, *CX_CLOCKS, *CX2_CLOCKS)


def valid_clock(value):
    return type(value) is int and value in CLOCKS


def clock_label(value):
    return "Entry clock" if value == 0 else f"{value} MHz"


def parse_clock(text):
    text = text.strip()
    if text.casefold() == "entry clock":
        return 0
    if text.casefold().endswith("mhz"):
        text = text[:-3].strip()
    if text.isascii() and text.isdecimal() and valid_clock(int(text)):
        return int(text)
    raise ValueError("Use Entry clock, or CX II 396–492 / CX 132–240 MHz in steps of 12.")
