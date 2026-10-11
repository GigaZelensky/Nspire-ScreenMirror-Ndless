"""Check the hardware experiment's cache geometry and SRAM exclusion rules."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "cache-plan.c"
source.write_text("""
#include "cache_plan.h"
#include <assert.h>
int main(void) {
    unsigned type = (4U<<18) | (2U<<15) | (2U<<12);
    assert(cache_plan_supported(type,5,0xfff0));
    assert(!cache_plan_supported(type,1,0xfff0));
    assert(!cache_plan_supported(type,5,0xfff1));
    assert(!cache_plan_supported(type ^ (1U<<18),5,0xfff0));
    assert(!cache_plan_supported(type ^ (1U<<15),5,0xfff0));
    assert(!cache_plan_supported(type ^ (1U<<12),5,0xfff0));
    assert(!cache_plan_supported(type | (1U<<14),5,0xfff0));
    assert(sram_plan_supported(0x202,0xa4004000,0xa4000c1a,1,1));
    assert(!sram_plan_supported(0x101,0xa4004000,0xa4000c1a,1,1));
    assert(!sram_plan_supported(0x202,0x12004000,0xa4000c1a,1,1));
    assert(!sram_plan_supported(0x202,0xa4004000,0xa4000c1e,1,1));
    assert(!sram_plan_supported(0x202,0xa4004000,0xa4000c1a,0,1));
    assert(!sram_plan_supported(0x202,0xa4004000,0xa4000c1a,1,0));
    assert(!sram_stack_clear(SRAM_TABLE_ADDRESS));
    assert(!sram_stack_clear(SRAM_TABLE_ADDRESS-1));
    assert(!sram_stack_clear(SRAM_TABLE_ADDRESS+COLOR_BYTES));
    assert(sram_stack_clear(0xa403f600));
    assert(sram_stack_clear(0x12000000));
    /* Each full block occupies at most 64 sets in its own way. Hot Y and
       clip subsets occupy distinct sets in a single way. Way 3 stays free. */
    unsigned seen[3][64] = {{0}};
    for (unsigned at=0; at<COLOR_BYTES; at+=32) {
        unsigned way=at/2048, set=(at/32)%64;
        assert(way<3 && !seen[way][set]++);
    }
    unsigned hot[64]={0};
    for (unsigned at=0; at<1024; at+=32) assert(!hot[(at/32)%64]++);
    for (unsigned at=5120; at<COLOR_BYTES; at+=32) assert(!hot[(at/32)%64]++);
    return 0;
}
""")
binary = out / ("cache-plan.exe" if sys.platform == "win32" else "cache-plan")
subprocess.run(
    [
        "gcc",
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(ROOT / "tools/benchmarks"),
        str(source),
        "-o",
        str(binary),
    ],
    check=True,
)
subprocess.run([str(binary)], check=True)
print("PASS benchmark cache geometry, set occupancy and SRAM mapping/stack/DMA exclusions")
