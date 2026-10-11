"""Reject unverified output mappings and modes before writing display memory."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "display-target.c"
source.write_text("""
#include "display_target.h"
#include <assert.h>
int main(void) {
    assert(mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c1a,640,0,1));
    assert(!mirror_direct_vram_eligible(0,1,0xa8000000,0xa8000c1a,640,0,1));
    assert(!mirror_direct_vram_eligible(1,0,0xa8000000,0xa8000c1a,640,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0x12000000,0xa8000c1a,640,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0x12000c1a,640,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c1e,640,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c19,640,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c1a,800,0,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c1a,640,1,1));
    assert(!mirror_direct_vram_eligible(1,1,0xa8000000,0xa8000c1a,640,0,0));
    return 0;
}
""")
binary = out / ("display-target.exe" if sys.platform == "win32" else "display-target")
subprocess.run(
    [
        "gcc",
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(ROOT / "calc"),
        str(source),
        "-o",
        str(binary),
    ],
    check=True,
)
subprocess.run([str(binary)], check=True)
print(
    "PASS direct VRAM gate: model, layout, pitch, physical mapping, cache policy, overlay and codec"
)
