"""Exact native idle image gates and reversible timing-literal ownership."""

from pathlib import Path
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
fixture = json.loads((ROOT / "checks/fixtures/idle-timing.json").read_text())
with tempfile.TemporaryDirectory(prefix="mirror-idle-timing-") as directory:
    directory = Path(directory)
    source = directory / "test.c"
    source.write_text(
        '#include "idle_timing.h"\n#include <assert.h>\nstatic uint32_t words[]={'
        + ",".join(f"{v}U" for v in fixture["words"])
        + r"""};
static unsigned writes;
static bool readable=true,writable=true;
static uint32_t pointer=MIRROR_IDLE_ENTRY;
static bool read_word(void*c,uint32_t at,uint32_t *value) {
    (void)c;if(!readable)return false;
    if(at==0x10c52e3cU){*value=pointer;return true;}
    if(at<MIRROR_IDLE_ENTRY||at>=MIRROR_IDLE_ENTRY+344U||(at&3))return false;
    *value=words[(at-MIRROR_IDLE_ENTRY)/4];return true;
}
static bool write_word(void*c,uint32_t at,uint32_t value) {
    (void)c;assert(at==MIRROR_IDLE_LITERAL&&(value==0x502||value==0x528));
    if(!writable)return false;
    words[84]=value;++writes;return true;
}
static const MirrorTimingAccess io={0,read_word,write_word};
int main(void) {
    assert(mirror_timing_resolve(&io,0x202,0xa4000c12));
    assert(mirror_timing_resolve(&io,0x202,0xa4000c1a)); /* Measured NC, buffered mapping. */
    for(unsigned i=0;i<86;++i){words[i]^=1;assert(!mirror_timing_resolve(&io,0x202,0xa4000c12));words[i]^=1;}
    assert(!mirror_timing_resolve(&io,0x101,0xa4000c12));
    assert(!mirror_timing_resolve(&io,0x202,0xa4100c12));
    assert(!mirror_timing_resolve(&io,0x202,0xa4000c16));
    assert(!mirror_timing_resolve(&io,0x202,0xa4000c1e)); /* Cached/write-back rejected. */
    assert(!mirror_timing_resolve(&io,0x202,0xa4000812));
    pointer+=4;assert(!mirror_timing_resolve(&io,0x202,0xa4000c12));pointer-=4;
    readable=false;assert(!mirror_timing_resolve(&io,0x202,0xa4000c12));readable=true;
    MirrorIdleTiming p={0};assert(!mirror_timing_hold(&p,&io)&&!writes);
    p.supported=true;
    assert(mirror_timing_hold(&p,&io)&&words[84]==0x528&&p.held&&writes==1);
    assert(mirror_timing_hold(&p,&io)&&writes==1);
    assert(mirror_timing_release(&p,&io)&&words[84]==0x502&&!p.held&&writes==2);
    assert(mirror_timing_release(&p,&io)&&writes==2);
    assert(mirror_timing_hold(&p,&io));
    words[84]=0x502; /* Native image reinitialization, not a foreign patch. */
    assert(mirror_timing_release(&p,&io)&&!p.held&&words[84]==0x502);
    assert(mirror_timing_hold(&p,&io));words[7]^=1;unsigned before=writes;
    assert(!mirror_timing_release(&p,&io)&&!p.held&&writes==before);words[7]^=1;words[84]=0x502;
    /* Failed write can be verified as unchanged; failed read/write retain
       enough ownership to retry without changing any native instructions. */
    writable=false;assert(!mirror_timing_hold(&p,&io)&&p.held&&words[84]==0x502);
    assert(mirror_timing_release(&p,&io)&&!p.held&&p.status==0);writable=true;
    assert(mirror_timing_hold(&p,&io));readable=false;
    assert(!mirror_timing_release(&p,&io)&&p.held);readable=true;
    writable=false;assert(!mirror_timing_release(&p,&io)&&p.held);writable=true;
    assert(mirror_timing_release(&p,&io)&&!p.held);
    assert(mirror_timing_hold(&p,&io));
    assert(mirror_timing_after_clock(&p,&io,true,false,false,396)&&!p.held);
    assert(mirror_timing_hold(&p,&io));
    assert(mirror_timing_after_clock(&p,&io,true,false,true,492)&&p.held);
    assert(mirror_timing_after_clock(&p,&io,true,false,false,0)&&p.held);
    assert(mirror_timing_after_clock(&p,&io,false,true,true,396)&&!p.held);
    return 0;
}
"""
    )
    executable = directory / "test.exe"
    subprocess.run(
        [
            shutil.which("gcc"),
            "-std=c99",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "calc"),
            str(source),
            "-o",
            str(executable),
        ],
        check=True,
    )
    subprocess.run([str(executable)], check=True)
print(
    "PASS idle timing: complete image, mapping/pointer gates, one-word restore, reinit, foreign changes and rollback"
)
