from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build/checks/profile"
out.mkdir(parents=True, exist_ok=True)
(out / "os.h").write_text("void *TCC_Current_Task_Pointer(void);\n")
(out / "registers.h").write_text(
    "#include <stdint.h>\nuint32_t profile_read_register(uintptr_t);\n#define REG(at) profile_read_register(at)\n"
)
(out / "main.c").write_text(r"""
#include "profile.h"
#include "wire.h"
#include "sram_tables.h"
#include <assert.h>
#include <stdio.h>
static uint32_t counter=100,value=8000,pending=0,control=0xe2,task=0x1100;
uint32_t profile_read_register(uintptr_t at){switch(at){case 0x900c0000U:return 9999;case 0x900c0004U:return value;case 0x900c0008U:return control;case 0x900c0010U:return pending;default:return 0;}}
void*TCC_Current_Task_Pointer(void){return(void*)(uintptr_t)task;}
#if MIRROR_SRAM_TABLES
void mirror_tables_words(uint32_t words[32]) { for(unsigned i=0;i<32;i++)words[i]=0;words[0]=1;words[1]=1;words[3]=12;words[30]=5504; }
#endif
int main(int argc,char**argv){assert(argc==2);profile_bind_main();task=0x2200;profile_start(&counter,100);
assert(profile.high_resolution&&profile.hz==1000000&&profile.main_task==0x1100);
uint64_t start=profile_now();assert(start==1001999);value=9900;pending=1;
assert(profile_now()==1010099);profile_add(PROFILE_DECODE,start);assert(profile.time[PROFILE_DECODE]==8100);
counter=101;pending=0;assert(profile_now()==1010099);profile_stop();assert(!profile.enabled);
uint8_t reply[360];MirrorQueue q;mirror_queue_init(&q);q.session=73;mirror_reply(reply,&q,0,1440);unsigned bytes=profile_reply(reply);assert(bytes==40+MIRROR_PROFILE_WORDS*4);
FILE*f=fopen(argv[1],"wb");assert(f&&fwrite(reply,1,bytes,f)==bytes);fclose(f);
counter=200;control=0x80;profile_start(&counter,100);assert(!profile.high_resolution&&profile.hz==100);
return 0;}
""")
exe = out / "profile.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(out),
        "-I" + str(ROOT / "calc"),
        "-include",
        str(out / "registers.h"),
        str(ROOT / "calc/profile.c"),
        str(out / "main.c"),
        "-o",
        str(exe),
    ],
    check=True,
)
subprocess.run([str(exe), str(out / "reply.bin")], check=True)
sys.path.insert(0, str(ROOT / "pc"))
from wire import acknowledgement

profile = acknowledgement((out / "reply.bin").read_bytes(), 73)["profile"]
assert (
    profile["hz"] == 1000000
    and profile["elapsed_ticks"] == 8100
    and profile["decode_convert_ticks"] == 8100
)
experimental = out / "profile-sram.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DMIRROR_SRAM_TABLES=1",
        "-I" + str(out),
        "-I" + str(ROOT / "calc"),
        "-include",
        str(out / "registers.h"),
        str(ROOT / "calc/profile.c"),
        str(out / "main.c"),
        "-o",
        str(experimental),
    ],
    check=True,
)
subprocess.run([str(experimental), str(out / "reply-sram.bin")], check=True)
experimental_profile = acknowledgement((out / "reply-sram.bin").read_bytes(), 73)["profile"]
assert experimental_profile["sram_tables"]["sram_frames"] == 12
assert experimental_profile["decode_convert_ticks"] == 8100
assert (
    not profile["enabled"] and profile["high_resolution"] and profile["foreground_task"] == 0x1100
)
print(
    "PASS read-only profiler timer interpolation, masked-IRQ rollover, stopped duration, explicit coarse-clock mode and C/PC stats interop"
)
