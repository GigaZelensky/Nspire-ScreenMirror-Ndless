from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "calc/main.c").read_text()
body = text[
    text.index("static void service(") : text.index(
        "\nstatic void service_abort_detached(", text.index("static void service(")
    )
]
native = text[
    text.index("static int16_t channel_read(") : text.index(
        "/* OS calls occur", text.index("static int16_t channel_read(")
    )
]
# Host pointer width differs from the SDK's 32-bit ARM pointer argument.
body = native + body
body = body.replace("(uint32_t)(uintptr_t)", "(uintptr_t)").replace(
    "void *, unsigned, uint32_t)", "void *, unsigned, uintptr_t)"
)
code = (
    r"""
#include "wire.h"
#include "input.h"
#include "profile.h"
#include "display_rate.h"
#include <assert.h>
#include <stdint.h>
typedef int nn_ch_t;
MirrorProfile profile;static bool frame_active,decode_cpu;static const volatile uint32_t *os_counter;static uint32_t os_hz=100;
uint64_t profile_now(void){return 0;}void profile_add(unsigned stage,uint64_t start){(void)stage;(void)start;}void profile_start(const volatile uint32_t*c,uint32_t h){(void)c;(void)h;profile.enabled=true;}void profile_stop(void){profile.enabled=false;}unsigned profile_reply(uint8_t*p){memset(p+32,0,124);memcpy(p+32,"NDS1",4);wire_put(p+36,29);return 156;}void*TCC_Current_Task_Pointer(void){return (void*)1;}
static MirrorQueue queue;static MirrorInput input_queue;static bool closing;static unsigned callbacks,packet_limit=1440;
static volatile MirrorDisplayRate display_rate;
static int transport_error,clock_pending=-1;static unsigned reads,writes,count,close_on_read,irq_mask=192,stop_calls,after_pause_reads;static int16_t stop_result;
static uint8_t packet[320][1440],reply[INPUT_REPLY];static unsigned sizes[320];
static uint8_t service_input[MIRROR_PACKET],service_reply[INPUT_REPLY];
static unsigned close_waits,waits,clock_restores,log_writes;static int16_t close_result;static bool clock_restore_ok=true;
unsigned native_critical_enter(void){unsigned saved=irq_mask;irq_mask=192;return saved;}void native_critical_leave(unsigned n){irq_mask=n;}
unsigned native_interrupt_mask(void){return irq_mask;}
uint32_t __wrap_SDL_GetTicks(void){return 0;}
bool performance_clock_valid_mhz(unsigned n){return n==0||n==396||n==492;}
unsigned current_mhz(void){return 396;}unsigned capabilities(void){return 31;}
uint16_t native_read(nn_ch_t ch,unsigned ms,void*out,unsigned cap,uintptr_t length){(void)ch;assert(irq_mask==0&&(ms==100||ms==750));if(reads==count){if(queue.paused)++after_pause_reads;return (uint16_t)-1;}assert(sizes[reads]<=cap);memcpy(out,packet[reads],sizes[reads]);*(uint32_t*)length=sizes[reads++];if(close_on_read&&reads==close_on_read)closing=true;return 0;}
int native_usb_ui_wait(void){++waits;return 0;}
bool performance_clock_restore(void){assert(!callbacks);++clock_restores;return clock_restore_ok;}
static void write_log(const char*stage,int result){(void)stage;(void)result;assert(!callbacks);++log_writes;}
int16_t native_write(nn_ch_t ch,void*data,unsigned n){(void)ch;assert(irq_mask==0&&n>=32&&n<=INPUT_REPLY);memcpy(reply,data,n);++writes;return 0;}
static uintptr_t channel_read_export=(uintptr_t)native_read,channel_read_target;
static uintptr_t channel_write_export=(uintptr_t)native_write,current_task_export=(uintptr_t)TCC_Current_Task_Pointer;
static unsigned callback_io;static uintptr_t callback_task;static uint32_t callback_io_started,maximum_read_ms,maximum_write_ms;
int16_t TI_NN_StopService(unsigned id){assert(id==MIRROR_SERVICE&&!callbacks&&irq_mask==0);++stop_calls;return stop_result;}
"""
    + body
    + r"""
static void put(unsigned n,unsigned offset,unsigned flags){uint8_t*p=packet[n];memset(p,0,35);memcpy(p,"NDM1",4);p[4]=1;p[5]=2;p[6]=flags;wire_put(p+8,73);wire_put(p+12,1);wire_put(p+16,6);wire_put(p+20,offset);p[24]=3;sizes[n]=35;}
static void setup(void){mirror_queue_init(&queue);queue.session=73;input_reset(&input_queue,false);reads=writes=0;count=2;clock_pending=-1;closing=false;close_on_read=0;}
int main(void){
setup();count=1;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_DISPLAY_RATE;wire_put(packet[0]+8,73);wire_put(packet[0]+12,240);sizes[0]=32;service(1,0);assert(writes==1&&display_rate.valid&&display_rate.tenths==240);
uint32_t length=0;assert(channel_read(1,service_input,&length)==-1&&irq_mask==192);
channel_read_target=channel_read_export;assert(channel_read(1,service_input,&length)==-1&&irq_mask==192);
assert(channel_current_task()==(void*)1);
setup();put(0,0,1|4|8);put(1,3,2|4|8);service(1,0);assert(reads==2&&writes==1&&queue.received==1&&!callbacks&&callback_task==1);
setup();put(0,0,1|4);put(1,3,2|4);service(1,0);assert(reads==2&&writes==2&&queue.received==1);
setup();put(0,0,1|4|8);put(1,4,2|4|8);service(1,0);assert(writes==1&&reply[5]==MIRROR_BAD&&queue.need_key&&!queue.received);
setup();put(0,0,1|8);put(1,3,2|8);packet[0][5]=packet[1][5]=MIRROR_PROBE;service(1,0);assert(writes==1&&!queue.received&&reply[5]==MIRROR_OK);
setup();count=1;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_INPUT;wire_put(packet[0]+8,73);wire_put(packet[0]+12,1);sizes[0]=32;service(1,0);assert(input_queue.enabled&&input_queue.count==1&&memcmp(reply+32,"NDE1",4)==0&&reply[44]==INPUT_RELEASE);
reads=writes=0;packet[0][5]=MIRROR_POLL;wire_put(packet[0]+12,0);wire_put(packet[0]+28,1);service(1,0);assert(!input_queue.count&&input_queue.acknowledged==1);
setup();count=3;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_STREAM;wire_put(packet[0]+8,73);sizes[0]=32;
put(1,0,1|4|8);put(2,3,2|4|8);service(1,0);assert(reads==3&&writes==2&&queue.received==1&&!callbacks);
/* The 300-fragment budget resets at frame boundaries on a long-lived channel. */
setup();count=310;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_STREAM;wire_put(packet[0]+8,73);sizes[0]=32;
for(unsigned i=1;i<count;++i){put(i,0,1|2|8);packet[i][5]=MIRROR_PROBE;wire_put(packet[i]+16,3);}
service(1,0);assert(reads==310&&writes==310&&!callbacks&&!queue.received);
/* PAUSE keeps servicing the channel until the native peer-close read error. */
setup();count=2;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_STREAM;wire_put(packet[0]+8,73);sizes[0]=32;
memcpy(packet[1],packet[0],32);packet[1][5]=MIRROR_PAUSE;sizes[1]=32;
service(1,0);assert(reads==2&&writes==2&&!callbacks&&queue.paused&&!input_queue.enabled&&after_pause_reads==1);
/* Closing during a read replies CLOSING and never consumes/enqueues video. */
setup();put(0,0,1|2|4|8);wire_put(packet[0]+16,3);close_on_read=1;service(1,0);
assert(reads==1&&writes==1&&reply[5]==MIRROR_CLOSING&&!queue.received&&!callbacks);
/* A short idle read does not emit BAD or desynchronize the next real reply. */
setup();count=3;memset(packet[0],0,32);memcpy(packet[0],"NDM1",4);packet[0][4]=1;packet[0][5]=MIRROR_STREAM;wire_put(packet[0]+8,73);sizes[0]=32;
sizes[1]=0;put(2,0,1|2|4|8);wire_put(packet[2]+16,3);service(1,0);assert(reads==3&&writes==2&&queue.received==1&&!callbacks);
setup();sizes[0]=0;close_on_read=1;service(1,0);assert(reads==1&&!writes&&!callbacks);
callbacks=1;assert(!service_stop_step()&&!stop_calls);callbacks=0;
assert(service_stop_step()&&stop_calls==1&&irq_mask==192&&waits==2&&clock_restores==1&&log_writes==1);
stop_result=-1;assert(!service_stop_step()&&stop_calls==2);
clock_restore_ok=false;assert(!service_stop_step()&&stop_calls==2&&close_result==-1);
return 0;}
"""
)
out = ROOT / "build/checks"
out.mkdir(parents=True, exist_ok=True)
source = out / "service.c"
source.write_text(code)
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-O2",
        "-Wall",
        "-Wextra",
        "-I" + str(ROOT / "calc"),
        str(source),
        "-o",
        str(out / "service.exe"),
    ],
    check=True,
)
subprocess.run([str(out / "service.exe")], check=True)
print(
    "PASS production service logic: one reply per bulk frame, legacy replies, malformed fragment recovery and probe without decoding"
)
