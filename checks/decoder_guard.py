"""Diagnostic yield checks detect changed decoder-owned allocations safely."""

from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CODECS = ROOT / "vendor/ndvideo/src/codecs"
with tempfile.TemporaryDirectory(prefix="mirror-decode-guard-") as directory:
    directory = Path(directory)
    source = directory / "guard.c"
    source.write_text(
        r"""
#define MIRROR_DECODE_GUARD 1
#include "decoder_guard.h"
#include "decoder_input.h"
#include <assert.h>
static storage_t storage;
static mbStorage_t mb[300];
static macroblockLayer_t layer;
static u32 map[300];
static seqParamSet_t sps;
static picParamSet_t pps;
static MirrorFrame frame;
static MirrorDecoderInput input_copy;
int main(int argc, char **argv) {
    assert(argc==2);
    MirrorDecoder decoder={0};
    decoder.storage=&storage;
    storage.picSizeInMbs=300;
    storage.slice->decodeInProgress=1;
    storage.mb=mb; storage.mbLayer=&layer; storage.sliceGroupMap=map;
    storage.activeSps=&sps; storage.activePps=&pps;
    frame.total=19; frame.id=1;
    MirrorDecodeGuard guard={0};
    /* The initial input includes normal in-place decoder edits. */
    frame.data[3]=7;
    assert(mirror_guard_begin(&guard,&decoder,&frame));
    assert(!mirror_guard_end(&guard));
    assert(guard.checks==1 && !guard.changes);
    assert(!mirror_guard_end(&guard) && guard.checks==1);
    for(unsigned i=0;i<MIRROR_GUARD_REGIONS;++i) {
        assert(mirror_guard_begin(&guard,&decoder,&frame));
        unsigned char *byte=(unsigned char *)guard.region[i].address;
        *byte^=1;
        assert(mirror_guard_end(&guard)==(1U<<i));
        *byte^=1;
    }
    /* After-yield comparison must use saved allocations, even if a pointer
       in the decoder changed to an invalid address. */
    assert(mirror_guard_begin(&guard,&decoder,&frame));
    decoder.storage=(storage_t *)(uintptr_t)1;
    assert(mirror_guard_end(&guard)==2);
    decoder.storage=&storage;
    assert(guard.changes==9);
    storage.slice->decodeInProgress=0;
    assert(!mirror_guard_begin(&guard,&decoder,&frame));
    assert(!guard.armed && !mirror_guard_end(&guard));
    /* Unaligned buffers and tail bytes are included. */
    unsigned char data[12]={0};
    for(unsigned offset=0;offset<4;++offset) {
        uint32_t before=mirror_guard_hash(data+offset,7);
        data[offset+6]^=1;
        assert(before!=mirror_guard_hash(data+offset,7));
        data[offset+6]^=1;
    }
    frame.total=5; frame.id=73;
    memcpy(frame.data,"hello",5);
    assert(mirror_input_capture(&input_copy,29,&frame));
    assert(input_copy.hash==0x4f9f2cabU && input_copy.session==29 && input_copy.frame==73);
    frame.data[0]='X';
    assert(!memcmp(input_copy.data,"hello",5));
    assert(mirror_input_save(&input_copy,argv[1]));
    FILE *file=fopen(argv[1],"rb"); assert(file);
    unsigned char copied[8]={0};
    assert(fread(copied,1,sizeof(copied),file)==5);
    assert(!memcmp(copied,"hello",5)); fclose(file);
    frame.total=MIRROR_H264_FRAME+1;
    assert(!mirror_input_capture(&input_copy,29,&frame));
    assert(input_copy.bytes==5 && input_copy.hash==0x4f9f2cabU);
    return 0;
}
"""
    )
    executable = directory / "guard.exe"
    subprocess.run(
        [
            shutil.which("gcc"),
            "-std=c99",
            "-O2",
            "-fno-strict-aliasing",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "calc"),
            "-I" + str(CODECS),
            str(source),
            "-o",
            str(executable),
        ],
        check=True,
    )
    subprocess.run([str(executable), str(directory / "input.bin")], check=True)
print("PASS diagnostic yield guard: eight regions, changed pointers, unaligned input and padding")
print(
    "PASS original-input capture: known FNV vector, independent copy, exact file bytes and bounds"
)
