"""Run the actual calculator scanner against SDK key definitions and mock hardware."""

from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))
from tools.build_config import find_sdk

SDK = find_sdk()
out = ROOT / "build/checks/input-scan"
out.mkdir(parents=True, exist_ok=True)
(out / "os.h").write_text(r"""
#ifndef MOCK_OS_H
#define MOCK_OS_H
#include <stdint.h>
#include <stdbool.h>
#include <keys.h>
typedef struct {uint16_t width,height;} touchpad_info_t;
typedef struct {uint8_t contact,proximity;uint16_t x,y;uint8_t xv,yv;uint16_t dummy;uint8_t pressed,arrow;} touchpad_report_t;
extern bool is_touchpad;
bool isKeyPressed(const t_key*);
#define isKeyPressed(x) isKeyPressed(&x)
touchpad_info_t* touchpad_getinfo(void);
int touchpad_scan(touchpad_report_t*);
#endif
""")
(out / "main.c").write_text(r"""
#include <os.h>
#include "input.h"
#include <assert.h>
bool is_touchpad=true;static uint16_t down[16];static touchpad_report_t report;static unsigned scans;
bool (isKeyPressed)(const t_key*k){return (down[(k->tpad_row-0x10)/2]&k->tpad_col)!=0;}
touchpad_info_t* touchpad_getinfo(void){static touchpad_info_t info={1000,1000};return &info;}
int touchpad_scan(touchpad_report_t*r){*r=report;++scans;return 0;}
static void set(const t_key*k,bool value){unsigned i=(k->tpad_row-0x10)/2;if(value)down[i]|=k->tpad_col;else down[i]&=~k->tpad_col;}
int main(void){MirrorInput q;input_reset(&q,true);mirror_input_scan(&q,0,false);assert(!q.count&&scans==1);
set(&KEY_NSPIRE_SHIFT,true);set(&KEY_NSPIRE_A,true);mirror_input_scan(&q,10,false);assert(q.count==2&&q.events[0].code==0x10&&q.events[1].code==65&&scans==2);
set(&KEY_NSPIRE_A,false);set(&KEY_NSPIRE_SHIFT,false);mirror_input_scan(&q,20,false);assert(q.count==4&&!q.events[3].flags);
input_reset(&q,true);mirror_input_scan(&q,30,false);report.contact=1;report.x=500;report.y=500;mirror_input_scan(&q,40,false);assert(!q.count);
report.x=510;report.y=520;mirror_input_scan(&q,50,false);assert(q.count==1&&q.events[0].kind==INPUT_MOVE&&q.events[0].x>0&&q.events[0].y<0);
report.pressed=1;report.arrow=TPAD_ARROW_CLICK;mirror_input_scan(&q,60,false);assert(q.events[q.count-1].kind==INPUT_BUTTON&&q.events[q.count-1].flags==1);
report.pressed=0;report.contact=0;mirror_input_scan(&q,70,false);assert(q.events[q.count-1].kind==INPUT_BUTTON&&q.events[q.count-1].flags==0);
input_reset(&q,true);mirror_input_scan(&q,80,false);set(&KEY_NSPIRE_CTRL,true);set(&KEY_NSPIRE_D,true);mirror_input_scan(&q,90,true);assert(q.count==1&&q.events[0].code==0x11);
set(&KEY_NSPIRE_D,false);set(&KEY_NSPIRE_CTRL,false);mirror_input_scan(&q,100,false);assert(q.count==2&&!q.events[1].flags);
set(&KEY_NSPIRE_A,true);mirror_input_scan(&q,110,false);mirror_input_scan(&q,470,false);assert(q.events[q.count-1].code==65&&q.events[q.count-1].flags==3);
set(&KEY_NSPIRE_A,false);input_reset(&q,true);mirror_input_scan(&q,480,false);
set(&KEY_NSPIRE_CTRL,true);set(&KEY_NSPIRE_TAB,true);mirror_input_scan(&q,490,true);assert(q.count==2&&q.events[0].code==0x11&&q.events[1].code==0x09&&q.events[1].flags==1);
set(&KEY_NSPIRE_CTRL,false);set(&KEY_NSPIRE_TAB,false);mirror_input_scan(&q,500,false);assert(q.count==4&&!q.events[2].flags&&!q.events[3].flags);
input_reset(&q,true);mirror_input_scan(&q,510,false);set(&KEY_NSPIRE_DOC,true);set(&KEY_NSPIRE_TAB,true);mirror_input_scan(&q,520,false);assert(q.count==2&&q.events[0].code==0x12&&q.events[1].code==0x09);
set(&KEY_NSPIRE_DOC,false);set(&KEY_NSPIRE_TAB,false);mirror_input_scan(&q,530,false);
input_reset(&q,true);mirror_input_scan(&q,540,false);set(&KEY_NSPIRE_FLAG,true);mirror_input_scan(&q,550,false);assert(!q.count);
return 0;}
""")
exe = out / "scan.exe"
subprocess.run(
    [
        shutil.which("gcc"),
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I" + str(out),
        "-I" + str(SDK / "include"),
        "-I" + str(ROOT / "calc"),
        str(ROOT / "calc/input.c"),
        str(out / "main.c"),
        "-o",
        str(exe),
    ],
    check=True,
)
subprocess.run([str(exe)], check=True)
print(
    "PASS production scanner: modifier/chord order, physical key release, touchpad direction/contact, click/drag, local shortcuts, repeat and single touchpad read per scan"
)
