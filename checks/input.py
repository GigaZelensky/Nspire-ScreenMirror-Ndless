"""Exercise the production event queue and host injection without touching other apps."""

from pathlib import Path
import sys
import subprocess
import shutil
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from remote_input import RemoteInput, PointerMotion
from wire import acknowledgement

out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "input-queue.c"
source.write_text(r"""
#include "input.h"
#include <assert.h>
#include <stdio.h>
int main(int argc,char**argv){assert(argc==2);MirrorInput q;uint8_t reply[INPUT_REPLY];MirrorQueue frame;
mirror_queue_init(&frame);frame.session=73;input_reset(&q,true);
input_push(&q,INPUT_KEY,1,65,0,0);input_push(&q,INPUT_KEY,0,65,0,0);
input_push(&q,INPUT_MOVE,0,0,100,200);input_push(&q,INPUT_MOVE,0,0,20,30);assert(q.count==3);
mirror_reply(reply,&frame,0,1440);unsigned n=input_reply(&q,reply);assert(n==76&&q.sent==3);
FILE*f=fopen(argv[1],"wb");assert(f&&fwrite(reply,1,n,f)==n);fclose(f);
input_push(&q,INPUT_MOVE,0,0,3,4);assert(q.count==4); /* sent events cannot be modified */
assert(!input_acknowledge(&q,5)&&q.count==4);assert(input_acknowledge(&q,2)&&q.count==2);
assert(input_acknowledge(&q,1)&&q.count==2); /* duplicate acknowledgement */
input_reset(&q,true);for(unsigned i=0;i<70;++i)input_push(&q,INPUT_KEY,1,65,0,0);
assert(q.overflows==1&&q.events[q.head].kind==INPUT_RELEASE&&q.count==7);
mirror_reply(reply,&frame,0,254);n=input_reply(&q,reply);assert(n<=254);
assert(input_acknowledge(&q,q.sent)&&!q.count);input_reset(&q,false);input_push(&q,INPUT_KEY,1,65,0,0);assert(!q.count);
return 0;}
""")
exe = out / "input-queue.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(ROOT / "calc"),
        str(source),
        "-o",
        str(exe),
    ],
    check=True,
)
subprocess.run([str(exe), str(out / "input-reply.bin")], check=True)
reply = (out / "input-reply.bin").read_bytes()
state = acknowledgement(reply, 73)
assert state["events"] == [(1, 3, 1, 65, 0, 0), (2, 3, 0, 65, 0, 0), (3, 1, 0, 0, 120, 230)]
for bad in [
    reply[:-1],
    reply[:36] + struct.pack("<I", 99) + reply[40:],
    reply[:44] + b"\x09" + reply[45:],
]:
    try:
        acknowledgement(bad, 73)
    except IOError:
        pass
    else:
        raise AssertionError("Malformed events accepted")


class Backend:
    def __init__(self):
        self.events = []

    def key(self, *args):
        self.events.append(("key", *args))

    def button(self, *args):
        self.events.append(("button", *args))

    def move(self, *args):
        self.events.append(("move", *args))

    def wheel(self, *args):
        self.events.append(("wheel", *args))


b = Backend()
remote = RemoteInput(dict(left=-1920, top=0, width=1920, height=1080), backend=b)
remote.apply(state["events"])
before = len(b.events)
remote.apply(state["events"])
assert len(b.events) == before
remote.apply([(4, 3, 1, 0x11, 0, 0), (5, 2, 1, 1, 0, 0), (6, 3, 1, 67, 0, 0), (7, 3, 3, 67, 0, 0)])
assert b.events[-1] == ("key", 67, True)
remote.release()
assert not remote.keys and not remote.buttons
assert b.events[-3:] == [("key", 67, False), ("key", 0x11, False), ("button", 1, False)]
assert remote.ack == 7
remote.apply([(8, 3, 0, 65, 0, 0)])
assert b.events[-1] == ("button", 1, False)  # no foreign key release
print(
    "PASS C/PC event interop, ordered retry/ack, motion coalescing, overflow release, repeat, held-key cleanup and malformed replies"
)
for reverse in (False, True):
    for gain in (0.1, 0.5, 1.0, 2.0, 4.0):
        b = Backend()
        remote = RemoteInput(
            dict(left=0, top=0, width=640, height=480),
            backend=b,
            reverse_scroll=reverse,
            scroll_gain=gain,
        )
        events = [(n, 4, 0, 0, 0, 10) for n in range(1, 101)]
        remote.apply(events)
        amounts = [event[1] for event in b.events if event[0] == "wheel"]
        expected = int(100 * 10 * 120 * 12 * gain / 32767)
        assert sum(amounts) == expected * (1 if reverse else -1)
        before = b.events.copy()
        remote.apply(events)
        assert b.events == before, "Retried scroll events were applied twice"
        remote.release()
        assert remote.scroll == 0
print(
    "PASS default/reversed scrolling, sensitivity scaling, fractional accumulation and retry ordering"
)
now = [0.0]
b = Backend()
motion = PointerMotion(b, dict(left=0, top=0, width=2560, height=1440), clock=lambda: now[0])
for i in range(80):
    now[0] += 0.01
    motion.feed(0.7 if i % 2 else -0.7, 0.3 if i % 2 else -0.3)
    motion.tick()
assert not b.events, "Stationary sensor wobble moved the pointer"
motion.freeze()
assert not b.events, "Clicking released a jitter tail"
motion.reset()
positions = []
motion.feed(100, 0)
for i in range(12):
    now[0] += 0.008
    motion.tick()
    positions.append(motion.emitted[0])
assert positions == sorted(positions) and 98 <= positions[-1] <= 100, positions
assert not any(event[2] for event in b.events if event[0] == "move"), (
    "Horizontal swipe wobbled vertically"
)
motion.feed(-100, 0)
for i in range(12):
    now[0] += 0.008
    motion.tick()
    assert 0 <= motion.emitted[0] <= 100
motion.freeze()
assert motion.emitted[0] == 0
motion.reset()
before = len(b.events)
motion.feed(1, 1)
motion.reset()
now[0] += 0.5
motion.tick()
assert len(b.events) == before
print(
    "PASS pointer hysteresis, straight-axis guard, bounded adaptive smoothing, fast settling, click stability and Stop cancellation"
)

# Real Windows structures with mocked position/injection: no live input sent.
if sys.platform == "win32":
    import ctypes as c
    from remote_input import WindowsInput

    desktop = {76: -1360, 77: 0, 78: 4560, 79: 1920}
    source = [100, 100]
    backend = WindowsInput()
    backend.metric = desktop.__getitem__

    def position(pointer):
        point = c.cast(pointer, c.POINTER(backend.Point)).contents
        point.x, point.y = source
        return True

    backend.position = position
    emitted = []
    backend.emit = lambda value: emitted.append((value.mouse.dx, value.mouse.dy, value.mouse.flags))
    region = dict(left=2560, top=1440, width=640, height=480)
    # Free movement must use deltas even if a game continually recentres its cursor.
    backend.position = lambda pointer: (_ for _ in ()).throw(AssertionError("Cursor read"))
    backend.move(10, 0, region)
    assert emitted[-1] == (10, 0, 1)
    source[:] = [2560, 1500]
    backend.move(-20, 7, region)
    assert emitted[-1] == (-20, 7, 1)
    backend.confine = True
    backend.position = position
    source[:] = [100, 100]
    backend.move(10, 0, region)
    assert abs(-1360 + emitted[-1][0] * 4559 / 65535 - 2890) <= 1
    source[:] = [2560, 1500]
    backend.move(-20, 0, region)
    assert abs(-1360 + emitted[-1][0] * 4559 / 65535 - 2560) <= 1
    print(
        "PASS relative mouse deltas without cursor warps; optional confinement centres/clips explicitly"
    )

    # Inspect production KEYBDINPUT packets without sending input to any application.
    packets = []
    backend.emit = lambda value: packets.append(
        (value.type, value.keyboard.key, value.keyboard.scan, value.keyboard.flags)
    )
    backend.foreground = lambda: 123
    backend.window_thread = lambda window, process: 456 if window == 123 else 0
    backend.layout = lambda thread: 789 if thread == 456 else 0
    scans = {
        ord("W"): 0x11,
        ord("A"): 0x1E,
        ord("S"): 0x1F,
        ord("D"): 0x20,
        0x1B: 0x01,
        0x10: 0x2A,
        0x11: 0x1D,
        0x12: 0x38,
        0x26: 0xE048,
        0x6F: 0xE035,
        0x0D: 0x1C,
        0xBE: 0x34,
    }
    mappings = []

    def map_key(code, mode, layout):
        assert mode == 4 and layout == 789
        mappings.append(code)
        return scans[code]

    backend.map_key = map_key
    for code, scan in scans.items():
        flags = 8 | (1 if scan >> 8 == 0xE0 else 0)
        backend.key(code, True)
        backend.key(code, True)
        backend.key(code, False)
        assert packets[-3:] == [(1, 0, scan & 0xFF, flags)] * 2 + [(1, 0, scan & 0xFF, flags | 2)]
    assert len(mappings) == len(scans) and not backend.held_scans
    backend.key(ord("W"), True)
    scans[ord("W")] = 0x2C  # Foreground layout changes during a held key.
    backend.key(ord("W"), False)
    assert packets[-1] == (1, 0, 0x11, 10)
    backend.key(ord("W"), True)
    assert packets[-1] == (1, 0, 0x2C, 8)

    def reject(value):
        raise OSError("Blocked injection")

    backend.emit = reject
    try:
        backend.key(ord("W"), False)
        raise AssertionError("Failed key release was accepted")
    except OSError:
        pass
    assert backend.held_scans[ord("W")] == 0x2C
    backend.emit = lambda value: None
    backend.key(ord("W"), False)
    for invalid in (0, 0xE11D):
        backend.map_key = lambda *args: invalid
        try:
            backend.key(ord("W"), True)
            raise AssertionError("Invalid key mapping was accepted")
        except OSError:
            pass
        assert not backend.held_scans
    print(
        "PASS scan-code WASD/Esc/modifiers/navigation, repeats, layout changes and failed releases"
    )
