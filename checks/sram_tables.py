"""Reject conflicting SRAM users and validate the extended timing/control wire format."""

from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from wire import ACK, acknowledgement, OK

out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "sram-table-gate.c"
source.write_text("""
#include "sram_table_gate.h"
#include "wire.h"
#include <assert.h>
int main(void) {
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,0,0,0,1,0)==TABLE_SRAM_USED);
    assert(mirror_table_gate(0x101,0xa4004000,0xa4000c1a,1,1,0,0,0,0,1,0)==TABLE_LAYOUT);
    assert(mirror_table_gate(0x202,0x12004000,0xa4000c1a,1,1,0,0,0,0,1,0)==TABLE_LAYOUT);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1e,1,1,0,0,0,0,1,0)==TABLE_LAYOUT);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,0,1,0,0,0,0,1,0)==TABLE_LAYOUT);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,0,0,0,0,0)==TABLE_LAYOUT);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,0,0,0,0,0,1,0)==TABLE_STACK);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,1,0,0,0,1,0)==TABLE_USB_BUSY);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,1,0,0,1,0)==TABLE_USB_BUSY);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,0,1,0,1,0)==TABLE_USB_BUSY);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,0,0,1,1,0)==TABLE_USB_BUSY);
    assert(mirror_table_gate(0x202,0xa4004000,0xa4000c1a,1,1,0,0,0,0,1,4)==TABLE_DMA_BUSY);
    assert(!mirror_table_stack_clear(MIRROR_TABLE_SRAM-32));
    assert(!mirror_table_stack_clear(MIRROR_TABLE_SRAM+MIRROR_COLOR_BYTES));
    assert(mirror_table_stack_clear(0xa403f600));
    uint8_t bytes[32]={0}; memcpy(bytes,"NDM1",4); bytes[4]=1;bytes[5]=MIRROR_STATS;
    wire_put(bytes+8,73); MirrorPacket packet;
    for(unsigned mode=3;mode<=4;mode++) {
        wire_put(bytes+12,mode);
        assert(mirror_parse(bytes,32,1440,&packet)==(MIRROR_SRAM_TABLES!=0));
    }
    wire_put(bytes+12,5); assert(!mirror_parse(bytes,32,1440,&packet));
    return 0;
}
""")
for enabled in (0, 1):
    binary = out / (f"sram-table-gate-{enabled}" + (".exe" if sys.platform == "win32" else ""))
    subprocess.run(
        [
            "gcc",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-DMIRROR_SRAM_TABLES={enabled}",
            "-I" + str(ROOT / "calc"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)

words = [0] * 80
words[0], words[1] = 3, 2100
words[48:58] = [1, 1, 2, 123, 456, 7, 0, 0, 0, 2100]
words[58:68] = [12345, 1, 23456, 2, 90, 0, 80, 0, 70, 0]
words[78] = 5504
prefix = ACK.pack(b"NDA1", 1, OK, 2, 0, 73, 4, 4, 1440, 4, 0)


def reply(values):
    return prefix + b"NDS1" + struct.pack("<I", len(values)) + struct.pack("<80I", *values)


data = reply(words)
stats = acknowledgement(data, 73)["profile"]["sram_tables"]
assert stats["sram_frames"] == 123 and stats["ram_frames"] == 456 and stats["enabled"]
assert stats["sram_convert_ticks"] == 12345 + (1 << 32)
assert stats["ram_convert_ticks"] == 23456 + (2 << 32)
assert stats["setup_ticks"] == 90 and stats["restore_ticks"] == 80 and stats["verify_ticks"] == 70
for bad in (data[:-1], reply(words[:78] + [1, 0])):
    try:
        acknowledgement(bad, 73)
    except OSError:
        pass
    else:
        raise AssertionError("Invalid SRAM timings accepted")
print(
    "PASS SRAM layout/stack/DMA guards, experimental-only mode controls and 64-bit timing replies"
)
