"""Check suspended-worker diagnostics never read outside mapped task/stack bounds."""

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "worker-wait.c"
source.write_text(r"""
#include "worker_wait.h"
#include <assert.h>
#include <string.h>
static uint32_t memory[2048];
static unsigned reads;
static uint32_t forbidden;
static bool allow(void*c,uint32_t a,uint32_t n,unsigned k){
 (void)c;(void)k;return !(a&3)&&a>=0x1000&&a<=0x3000&&n<=0x3000-a;
}
static bool word(void*c,uint32_t a,uint32_t*v){
 assert(allow(c,a,4,PORTABLE_DATA));reads++;if(a==forbidden)return false;
 *v=memory[(a-0x1000)/4];return true;
}
static PortableView view={0,allow,word,0,0,0x1000,0x3000,0};
static void put(unsigned offset,uint32_t v){memory[offset/4]=v;}
static void initialize(void){
 memset(memory,0,sizeof(memory));put(0xc,0x5441534b);put(0x18,0x12340004);
 put(0x24,0x1800);put(0x28,0x2ff8);put(0x2c,0x1f00);
 for(unsigned i=0;i<128;i++)memory[(0x1f00-0x1000)/4+i]=0x10001000+i*4;
 forbidden=0;reads=0;
}
static void reject(void){
 MirrorWorkerWait output,before;memset(&output,0x55,sizeof(output));before=output;
 assert(!mirror_worker_wait(&view,0x1000,&output));
 assert(!memcmp(&output,&before,sizeof(output)));
}
int main(void){
 initialize();MirrorWorkerWait output;
 assert(mirror_worker_wait(&view,0x1000,&output));assert(output.count==128);
 assert(output.task==0x1000&&output.state==0x12340004&&output.words[127]==0x100011fc);
 put(0x2c,0x2fb0);assert(mirror_worker_wait(&view,0x1000,&output));assert(output.count==19);
 initialize();put(0xc,0);reject();
 initialize();put(0x2c,0x1700);reject();
 initialize();put(0x2c,0x2ffc);reject();
 initialize();put(0x28,0xfffffff8);reject();
 initialize();put(0x24,0x2ffc);reject();
 initialize();put(0x2c,0x1f01);reject();
 initialize();forbidden=0x1f00+200;reject();
 assert(!mirror_worker_wait(&view,0xfffffff0,&output));
 assert(!mirror_worker_wait(&view,0x1001,&output));
 assert(!mirror_worker_wait(0,0x1000,&output));
 assert(!mirror_worker_wait(&view,0x1000,0));
 return 0;
}
""")
binary = out / "worker-wait.exe"
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
print("PASS worker snapshot: task identity, stack bounds, short stack, overflow and partial read")
