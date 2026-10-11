"""Check native disconnect witnesses and explicit-shutdown link validation."""

from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
abort = json.loads((ROOT / "checks/fixtures/nnse-abort.json").read_text())
disconnect = json.loads((ROOT / "checks/fixtures/connector-disconnect.json").read_text())
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "nnse-abort.c"
source.write_text(
    r"""
#include "nnse_abort.h"
#include <assert.h>
static uint32_t disconnect_words[]={"""
    + ",".join(f"{w}U" for w in disconnect["words"])
    + r"""};
static uint32_t abort_words[]={"""
    + ",".join(f"{w}U" for w in abort["words"])
    + r"""};
static bool access=true;static uint32_t mode=1;
static bool allow(void*c,uint32_t a,uint32_t n,unsigned k){
 (void)c;(void)k;if(!access)return false;
 return (a==0x10616aa8&&n==268)||(a==0x1061a254&&n==228)||(a==0x113e1bb4&&n==4);
}
static bool read_word(void*c,uint32_t a,uint32_t*v){
 (void)c;if(a==0x113e1bb4){*v=mode;return true;}
 if(a>=0x10616aa8&&a<0x10616bb4){*v=disconnect_words[(a-0x10616aa8)/4];return true;}
 if(a>=0x1061a254&&a<0x1061a338){*v=abort_words[(a-0x1061a254)/4];return true;}
 return false;
}
static PortableView view={0,allow,read_word,0x10000000,0x11000000,0x11000000,0x14000000,0};
static uint32_t target(void){return mirror_nnse_abort_target(&view,0x202,0x100bb1a4,0x10429200);}
int main(void){
 assert(target()==0x10616aa8);
 disconnect_words[9]^=1;assert(!target());disconnect_words[9]^=1;
 abort_words[14]^=1;assert(!target());abort_words[14]^=1;
 mode=0;assert(target()==0x10616aa8);mode=1;
 access=false;assert(!target());access=true;
 assert(!mirror_nnse_abort_target(&view,0x101,0x100bb1a4,0x10429200));
 assert(!mirror_nnse_abort_target(&view,0x202,0x100bb1a8,0x10429200));
 assert(!mirror_nnse_abort_target(0,0x202,0x100bb1a4,0x10429200));
 view.code_end=0x10616ab0;assert(!target());view.code_end=0x11000000;
 assert(mirror_nnse_detached(0x202,0x01000010,0x00310020));
 assert(!mirror_nnse_detached(0x202,0x01000010,0x00330020));
 assert(!mirror_nnse_detached(0x202,0,0x00310020));
 assert(!mirror_nnse_detached(0x202,0xffffffff,0x00310020));
 assert(!mirror_nnse_detached(0x202,0x01000010,0xffffffff));
 assert(!mirror_nnse_detached(0x202,0x01000010,0));
 assert(!mirror_nnse_detached(0x202,0x01000010,0x00210020));
 assert(!mirror_nnse_detached(0x101,0x01000040,0x00310020));
 assert(mirror_nnse_shutdown_link(0x202,0x01000010,0x00330020,true));
 assert(mirror_nnse_shutdown_link(0x202,0x01000010,0x00310020,true));
 assert(!mirror_nnse_shutdown_link(0x202,0x01000010,0x00330020,false));
 assert(!mirror_nnse_shutdown_link(0x202,0,0x00330020,true));
 assert(!mirror_nnse_shutdown_link(0x202,0x01000010,0x00300020,true));
 return 0;
}
"""
)
binary = out / "nnse-abort.exe"
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
main = (ROOT / "calc/main.c").read_text()
body = main[
    main.index("static void service_abort_detached(") : main.index(
        "static void capture_worker_wait("
    )
]
body = body.replace("*(const volatile uint32_t *)0xb0000000U", "test_cap")
body = body.replace("*(const volatile uint32_t *)0xb0000080U", "test_otg")
body = body.replace("*(const volatile uint32_t *)0x113e1bb4U", "test_mode")
source = out / "native-disconnect.c"
source.write_text(
    r"""
#include "nnse_abort.h"
#include <assert.h>
#include <string.h>
#define MIRROR_FORCE_NATIVE_ABORT 0
static uintptr_t nnse_abort_target;
static unsigned nnse_abort_attempts,callbacks,callback_io,mask,calls,logs,log_action;
static int nnse_abort_result;
static uint32_t nnse_capability,nnse_otg,nnse_detached_since,now,test_cap,test_otg;
static uint32_t nnse_ws_mode,test_mode;
static bool nnse_detached_seen,closing;
static uint32_t nspire_hardware_id(void){return 0x202;}
static uint32_t __wrap_SDL_GetTicks(void){return now;}
static unsigned native_interrupt_mask(void){return mask;}
static void native_critical_leave(unsigned value){mask=value;}
static void write_log(const char*stage,int result){
 (void)result;++logs;
 if(!strcmp(stage,"before_native_nnse_abort")){
  if(log_action==1)closing=false;
  if(log_action==2)test_otg=0;
 }
}
static int disconnect(int connected){assert(!connected&&mask==0);++calls;callbacks=0;callback_io=0;return 7;}
"""
    + body
    + r"""
static void setup(void){
 nnse_abort_target=(uintptr_t)disconnect;nnse_abort_attempts=0;callbacks=1;callback_io=2;
 nnse_detached_seen=false;now=0;mask=192;calls=logs=log_action=0;
 test_cap=0x01000010;test_otg=0x00310020;test_mode=1;nnse_abort_result=0;
 closing=true;
}
int main(void){
 setup();service_abort_detached();now=49;service_abort_detached();assert(!calls&&!logs);
 now=50;service_abort_detached();assert(calls==1&&logs==2&&nnse_abort_attempts==1&&nnse_abort_result==7&&mask==192);
 callbacks=1;callback_io=2;service_abort_detached();assert(calls==1);
 setup();test_otg|=1U<<17;callbacks=0;callback_io=0;service_abort_detached();now=50;service_abort_detached();assert(calls==1&&mask==192);
 setup();test_cap=0;service_abort_detached();assert(!nnse_detached_seen&&!calls);
 setup();closing=false;service_abort_detached();now=50;service_abort_detached();assert(!calls&&!nnse_detached_seen);
 setup();nnse_abort_target=0;service_abort_detached();assert(!calls);
 setup();test_mode=0;service_abort_detached();assert(!calls&&!nnse_detached_seen);now=50;service_abort_detached();assert(!calls&&!nnse_abort_attempts);
 setup();service_abort_detached();now=50;log_action=1;service_abort_detached();assert(!calls&&!nnse_abort_attempts);
 setup();service_abort_detached();now=50;log_action=2;service_abort_detached();assert(!calls&&!nnse_abort_attempts);
 setup();now=0xfffffff0;service_abort_detached();now=40;service_abort_detached();assert(calls==1);
 return 0;
}
"""
)
binary = out / "native-disconnect.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-function",
        "-I" + str(ROOT / "calc"),
        "-I" + str(ROOT / "vendor/ndvideo/src/platform"),
        str(source),
        "-o",
        str(binary),
    ],
    check=True,
)
subprocess.run([str(binary)], check=True)
print(
    "PASS native disconnect: firmware witnesses, physical gates, debounce, IRQ mask and log-yield races"
)
