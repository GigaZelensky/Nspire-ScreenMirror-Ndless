"""Validate bounded-read resolution against a recorded, relocated OS code witness."""

from pathlib import Path
import json
import struct
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]
fixture = json.loads((ROOT / "checks/fixtures/channel-read.json").read_text())
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "channel-read.c"
source.write_text(
    r"""
#include "channel_read.h"
#include <assert.h>
#include <string.h>
static uint32_t image[512];
static bool access=true;
static bool allow(void*c,uint32_t a,uint32_t n,unsigned kind){(void)c;(void)kind;return access&&a>=0x1000&&a<=0x1800&&n<=0x1800-a;}
static bool word(void*c,uint32_t a,uint32_t*v){if(!allow(c,a,4,PORTABLE_DATA))return false;*v=image[(a-0x1000)/4];return true;}
static PortableView view={0,allow,word,0x1000,0x1800,0x1000,0x1800,0};
static const uint32_t body[]={"""
    + ",".join(str(v) + "U" for v in fixture["body"])
    + r"""};
int main(void){
    memcpy(image+128,body,sizeof(body));
    uint32_t wrapper[]={0xe92d0030,0xe59f4014,0xe59fc014,0xe2415001,0xe1550004,0x91a0100c,0xe8bd0030,0xea000077,61998,62000};
    memcpy(image,wrapper,sizeof(wrapper));
    assert(mirror_channel_target(&view,0x1000)==0x1200);
    assert(mirror_channel_target(&view,0x1200)==0x1200);
    /* Relocated external calls/literal pools are permitted; internal control
     * flow, timeout constants and data access must retain their identities. */
    for(unsigned i=0;i<70;i++){
        uint32_t op=image[128+i];
        if((op&0x0f000000)==0x0b000000)image[128+i]^=0x1234;
        else if((op&0x0e5f0000)==0x041f0000)image[128+i]^=4;
    }
    assert(mirror_channel_target(&view,0x1000)==0x1200);
    image[128]^=1;assert(!mirror_channel_target(&view,0x1000));image[128]^=1;
    image[9]=750;assert(!mirror_channel_target(&view,0x1000));image[9]=62000;
    image[7]=0xea7fffff;assert(!mirror_channel_target(&view,0x1000));image[7]=0xea000077;
    access=false;assert(!mirror_channel_target(&view,0x1000));access=true;
    assert(!mirror_channel_target(&view,0x1001));assert(!mirror_channel_target(&view,0x1800));
    assert(!mirror_channel_target(NULL,0x1000));
    return 0;
}
"""
)
binary = out / "channel-read.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(ROOT / "calc"),
        "-I" + str(ROOT / "vendor/ndvideo/src/platform"),
        str(source),
        "-o",
        str(binary),
    ],
    check=True,
)
subprocess.run([str(binary)], check=True)
assert len(fixture["body"]) == 70
assert len(struct.pack("<70I", *fixture["body"])) == 280
print(
    "PASS native read witness: relocation, body mismatch, timeout clamp, invalid branch and inaccessible memory"
)
