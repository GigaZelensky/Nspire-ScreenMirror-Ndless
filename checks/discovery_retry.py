from pathlib import Path
import ctypes as c
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from transport import WindowsNavNet, Bridge

api = WindowsNavNet.__new__(WindowsNavNet)
calls = []
state = {"polls": 0, "destroyed": 0, "done": 0}
api.channel = c.c_void_p()
api.create = lambda: 1


def enum_init(op):
    state["polls"] += 1
    return -274 if state["polls"] <= 8 else 0


def next_node(op, pointer):
    if state["polls"] <= 10:
        return -274
    c.cast(pointer, c.POINTER(c.c_void_p))[0] = c.c_void_p(123)
    return 0


def connect(node, port, pointer):
    assert node.value == 123 and port == 0x4008
    c.cast(pointer, c.POINTER(c.c_void_p))[0] = c.c_void_p(456)
    calls.append("connect")
    return 0


api.enum_init = enum_init
api.next = next_node
api.connect = connect
api.destroy = lambda op: state.__setitem__("destroyed", state["destroyed"] + 1)
api.enum_done = lambda op: state.__setitem__("done", state["done"] + 1)
for i in range(10):
    assert api.begin() is False
assert (
    api.begin() is True and state["destroyed"] == 11 and state["done"] == 3 and calls == ["connect"]
)
bridge = Bridge.__new__(Bridge)
bridge.stop = threading.Event()
events = []


def command(kind, data=None):
    events.append(kind)
    return events.count("begin") > 3


bridge.command = command
with bridge.connection():
    events.append("exchange")
assert events == ["begin"] * 4 + ["exchange", "end"]
bridge.stop.set()
bridge.command = lambda kind, data=None: False
try:
    with bridge.connection():
        raise AssertionError("Cancelled discovery acquired a connection")
except InterruptedError:
    pass
print(
    "PASS empty discovery preserves one helper; enumeration handles release; later attachment connects; Stop cancels waiting"
)
