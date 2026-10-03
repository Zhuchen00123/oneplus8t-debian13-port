#!/usr/bin/env python3
"""Compile the actual driver receive/parser functions with host input stubs.

Synthetic wire packets test header accounting, bounds and events. This does
not simulate the touchscreen firmware or prove KB2000 touch compatibility.
"""
import argparse
from pathlib import Path
import re
import resource
import subprocess
import tempfile


def function(text, name):
    start = re.search(r'static (?:int|irqreturn_t) ' + name + r'\(', text).start()
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('driver', type=Path)
    p.add_argument('--compiler', default='gcc')
    a = p.parse_args()
    src = a.driver.read_text()
    header = re.search(r'struct tcm_message_header \{.*?\} __packed;', src, re.S)[0]
    report = re.search(r'struct tcm_default_report_data \{.*?\} __packed;', src, re.S)[0]
    constants = '\n'.join(re.findall(r'^#define REPORT_.*$', src, re.M))
    harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef int irqreturn_t;
#define __packed __attribute__((packed))
#define IRQ_HANDLED 1
#define MT_TOOL_FINGER 1
#define ABS_MT_POSITION_X 1
#define ABS_MT_POSITION_Y 2
#define ABS_MT_TOUCH_MAJOR 3
#define ABS_MT_TOUCH_MINOR 4
#define ABS_MT_PRESSURE 5
#define dev_dbg(...) ((void)0)
#define dev_err(...) (++errors)
#define dev_err_ratelimited(...) (++errors)
#define print_hex_dump_bytes(...) ((void)0)
#define swap(a,b) do { __typeof__(a) v = (a); (a) = (b); (b) = v; } while (0)
struct client { int dev; };
struct input { int unused; };
struct tcm_data { struct client *client; struct input *input; int response;
                  u16 buf_size; char buf[256]; };
static unsigned char wire[256];
static int events, syncs, completions, errors, last_x, last_y, last_slot;
static int completion_done(int *p) { return *p; }
static void complete(int *p) { *p=1; ++completions; }
static u16 get_unaligned_le16(const char *p) {
    return (unsigned char)p[0] | ((unsigned char)p[1] << 8);
}
static int tcm_recv_report(struct tcm_data *t, void *buf, size_t n) {
    (void)t; assert(n <= sizeof(wire)); memcpy(buf,wire,n); return 0;
}
static void input_mt_slot(struct input *i, int n) { (void)i; last_slot=n; }
static void input_mt_report_slot_state(struct input *i, int kind, int active) {
    (void)i; (void)kind; assert(active); ++events;
}
static void input_report_abs(struct input *i,int kind,int val) {
    (void)i; if(kind==ABS_MT_POSITION_X) last_x=val;
    if(kind==ABS_MT_POSITION_Y) last_y=val;
}
static void input_mt_sync_frame(struct input *i) { (void)i; ++syncs; }
static void input_sync(struct input *i) { (void)i; }
'''
    harness += '\n' + header + '\n' + report + '\n' + constants + '\n'
    harness += function(src, 'tcm_handle_touch_report') + '\n'
    harness += function(src, 'tcm_report_irq') + '\n'
    harness += r'''
static void packet(u8 code,u16 n) {
    memset(wire,0,sizeof(wire)); wire[0]=0xa5; wire[1]=code;
    wire[2]=n; wire[3]=n>>8;
    events=syncs=completions=errors=last_x=last_y=last_slot=0;
}
int main(void) {
    struct client c={0}; struct input i={0};
    struct tcm_data t={.client=&c,.input=&i};
    _Static_assert(sizeof(((struct tcm_default_report_data *)0)->points[0])==11,
                   "Fixture encodes the currently supported 11-byte format");
    /* Response: both length bytes and last two payload bytes must be copied. */
    packet(REPORT_OK,3); wire[4]=0x11; wire[5]=0x22; wire[6]=0x33;
    assert(tcm_report_irq(0,&t)==IRQ_HANDLED);
    assert(completions==1 && t.buf_size==7 && memcmp(t.buf,wire,7)==0);
    /* Full-size response is safe; one byte beyond the buffer is rejected. */
    packet(REPORT_OK,252); t.response=0; wire[255]=0x5a;
    tcm_report_irq(0,&t); assert(completions==1 && t.buf_size==256 && t.buf[255]==0x5a);
    packet(REPORT_OK,253); t.response=0; t.buf_size=17;
    tcm_report_irq(0,&t); assert(completions==0 && t.buf_size==17 && errors==1);
    packet(REPORT_OK,65535); t.response=0;
    tcm_report_irq(0,&t); assert(completions==0 && errors==1);
    packet(REPORT_OK,3); wire[0]=0; t.response=0;
    tcm_report_irq(0,&t); assert(completions==0 && errors==1);
    /* No touch objects: one fps byte is still a complete touch frame. */
    packet(REPORT_TOUCH,1); t.response=1;
    tcm_report_irq(0,&t); assert(events==0 && syncs==1 && errors==0);
    /* One object; construct bytes independently of the C bitfields. */
    packet(REPORT_TOUCH,12); t.response=1;
    wire[4]=60; wire[7]=0x12; wire[8]=0x23; wire[9]=0x61; wire[10]=0x45;
    tcm_report_irq(0,&t);
    assert(events==1 && syncs==1 && errors==0);
    assert(last_slot==2 && last_x==0x123 && last_y==0x456);
    packet(REPORT_TOUCH,23); t.response=1;
    tcm_report_irq(0,&t); assert(events==2 && syncs==1 && errors==0);
    /* Truncated object and zero payload must not parse or emit a frame. */
    packet(REPORT_TOUCH,11); t.response=1;
    tcm_report_irq(0,&t); assert(events==0 && syncs==0 && errors==1);
    packet(REPORT_TOUCH,0); t.response=1;
    tcm_report_irq(0,&t); assert(events==0 && syncs==0 && errors==1);
    for (size_t n=0;n<4;n++) {
        packet(REPORT_TOUCH,0);
        assert(tcm_handle_touch_report(&t,(char *)wire,n)==-EINVAL);
        assert(events==0 && syncs==0);
    }
    puts("TCM actual-function packet fixtures passed (ASan/UBSan)");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='kebab-tcm-reports-') as directory:
        root = Path(directory)
        (root/'test.c').write_text(harness)
        subprocess.run([a.compiler, '-std=gnu11', '-g', '-O1',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(root/'test.c'), '-o', str(root/'test')], check=True)
        subprocess.run([str(root/'test')], check=True, cwd=root,
                       preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))


if __name__ == '__main__':
    main()
