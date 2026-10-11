"""Host wall-clock FPS reaches the overlay independently of the calculator clock."""

from pathlib import Path
import subprocess
import shutil
import sys
import struct

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "pc"))
from wire import DISPLAY_RATE, CAP_DISPLAY_RATE, control

packet = control(DISPLAY_RATE, 73, 240)
assert packet[5] == 12 and struct.unpack_from("<I", packet, 12)[0] == 240
assert CAP_DISPLAY_RATE == 8192
out = root / "build/checks"
out.mkdir(parents=True, exist_ok=True)
path = out / "display-rate.c"
path.write_text(
    '#include <assert.h>\n#include "wire.h"\n#include "display_rate.h"\n'
    + "static unsigned char bytes[]={"
    + ",".join(map(str, packet))
    + "};\n"
    + r"""
int main(void){volatile MirrorDisplayRate rate={0};MirrorPacket p;MirrorQueue q;
mirror_queue_init(&q);q.session=73;
assert(MIRROR_CAP_DISPLAY_RATE==8192&&mirror_parse(bytes,32,1440,&p));
assert(mirror_accept(&q,&p)==MIRROR_OK&&p.frame==240);
assert(mirror_display_rate_update(&rate,p.frame)&&rate.valid&&rate.tenths==240);
/* Lost/slow local ticks cannot change the sender's 24.0 FPS measurement. */
assert(mirror_display_rate_update(&rate,450)&&rate.tenths==450);
assert(!mirror_display_rate_update(&rate,1001)&&rate.tenths==450);
wire_put(bytes+12,1001);assert(!mirror_parse(bytes,32,1440,&p));
wire_put(bytes+12,0);assert(mirror_parse(bytes,32,1440,&p));
assert(mirror_display_rate_update(&rate,0)&&rate.valid&&!rate.tenths);
mirror_display_rate_reset(&rate);assert(!rate.valid&&!rate.tenths);
wire_put(bytes+8,74);assert(mirror_parse(bytes,32,1440,&p)&&mirror_accept(&q,&p)==MIRROR_RESYNC);
return 0;}
"""
)
exe = out / "display-rate.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(root / "calc"),
        str(path),
        "-o",
        str(exe),
    ],
    check=True,
)
subprocess.run([str(exe)], check=True)
print("PASS C/PC wall-clock FPS packet, overlay bounds/reset and stale-session rejection")
