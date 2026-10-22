"""Failed decoder state cannot consume frames already buffered behind it."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import re

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="mirror-recovery-") as temporary:
    temporary = Path(temporary)
    source = temporary / "check.c"
    source.write_text(
        r"""
#include "wire.h"
#include <assert.h>
static MirrorQueue queue;
static void begin(void) {
    mirror_queue_init(&queue);
    MirrorPacket hello = {.session=73,.kind=MIRROR_HELLO};
    assert(mirror_accept(&queue,&hello)==MIRROR_OK);
    queue.reset=false;
}
static unsigned frame(unsigned id, unsigned flags) {
    static const uint8_t bytes[3]={1,2,3};
    MirrorPacket packet={73,id,3,0,3,MIRROR_FRAME_DATA,flags,bytes,0};
    return mirror_accept(&queue,&packet);
}
int main(void) {
    begin();
    assert(frame(1,MIRROR_FIRST|MIRROR_LAST|MIRROR_KEY)==MIRROR_OK);
    int active=mirror_take(&queue);
    assert(active>=0&&queue.frame[active].busy);
    assert(frame(2,MIRROR_FIRST|MIRROR_LAST)==MIRROR_OK);
    assert(queue.queued==1);
    mirror_queue_recover(&queue);
    assert(queue.reset&&queue.need_key&&!queue.queued&&queue.assembling==-1);
    assert(queue.frame[active].busy&&queue.frame[active].data[0]==1);
    assert(mirror_take(&queue)<0);
    assert(frame(3,MIRROR_FIRST|MIRROR_LAST)==MIRROR_RESYNC);
    queue.frame[active].busy=false;
    assert(mirror_credit(&queue)==2);
    assert(frame(3,MIRROR_FIRST|MIRROR_LAST|MIRROR_KEY)==MIRROR_OK);
    assert(queue.reset); /* main resets the decoder before taking this IDR */
    queue.reset=false;
    active=mirror_take(&queue);
    assert(active>=0&&queue.frame[active].id==3);
    queue.frame[active].busy=false;
    /* Incomplete fragments and late packets after PAUSE cannot revive it. */
    begin();
    static const uint8_t bytes[3]={4,5,6};
    MirrorPacket partial={73,1,6,0,3,MIRROR_FRAME_DATA,MIRROR_FIRST|MIRROR_KEY,bytes,0};
    assert(mirror_accept(&queue,&partial)==MIRROR_OK&&queue.assembling>=0);
    mirror_queue_recover(&queue);
    assert(queue.assembling==-1&&!queue.queued);
    MirrorPacket pause={.session=73,.kind=MIRROR_PAUSE};
    assert(mirror_accept(&queue,&pause)==MIRROR_OK&&queue.paused);
    assert(frame(2,MIRROR_FIRST|MIRROR_LAST|MIRROR_KEY)==MIRROR_RESYNC);
    begin();
    assert(frame(1,MIRROR_FIRST|MIRROR_LAST|MIRROR_KEY)==MIRROR_OK);
    return 0;
}
"""
    )
    executable = temporary / "check.exe"
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
main = (ROOT / "calc/main.c").read_text()
failure = main[
    main.index("unsigned index = decode_failure_count++") : main.index("frame->busy = false;")
]
assert failure.index("mirror_queue_recover(&queue)") < failure.index('write_log("decode_failure"')
reset = main[
    main.index("if (queue.reset && active < 0)") : main.index("if (queue.paused && active < 0")
]
assert reset.index('write_log("decoder_reset_begin"') < reset.index("mirror_decoder_free")
assert reset.index("mirror_decoder_free") < reset.index('write_log("h264_reset_done"')
print(
    "PASS failed-frame quarantine, queued/partial discard, retained busy ownership and clean-keyframe reset"
)

helper = re.search(r"static void waiting_for_keyframe\([^)]*\) \{.*?\n\}", main, re.S).group(0)
with tempfile.TemporaryDirectory(prefix="mirror-recovery-display-") as directory:
    directory = Path(directory)
    source = directory / "display.c"
    source.write_text(
        "#include <assert.h>\n#include <stdbool.h>\n"
        "static bool have_picture;static unsigned clears;\n"
        "static void waiting(const char *s){(void)s;++clears;}\n"
        + helper
        + '\nint main(void){waiting_for_keyframe("reset");assert(clears==1);'
        'have_picture=true;for(unsigned i=0;i<100;++i)waiting_for_keyframe("reset");'
        "assert(clears==1);return 0;}\n"
    )
    exe = directory / "display.exe"
    subprocess.run(
        [
            shutil.which("gcc"),
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(exe),
        ],
        check=True,
    )
    subprocess.run([str(exe)], check=True)
assert "have_picture = true;" in main
assert 'waiting_for_keyframe("Stream reset needed. Reconnecting...");' in failure
print("PASS recoverable reset retains a previously displayed picture instead of clearing the LCD")
