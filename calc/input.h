#ifndef NDMIRROR_INPUT_H
#define NDMIRROR_INPUT_H
#include "wire.h"
enum { INPUT_MOVE = 1, INPUT_BUTTON = 2, INPUT_KEY = 3, INPUT_WHEEL = 4, INPUT_RELEASE = 5 };
typedef struct {
    uint32_t sequence;
    uint8_t kind, flags;
    uint16_t code;
    int16_t x, y;
} MirrorEvent;
#define INPUT_QUEUE 64U
#define INPUT_REPLY 244U
typedef struct {
    MirrorEvent events[INPUT_QUEUE];
    unsigned head, count;
    uint32_t next, sent, acknowledged, overflows;
    bool enabled, reset_scan;
} MirrorInput;
static inline void input_reset(MirrorInput *q, bool enabled) {
    memset(q, 0, sizeof(*q));
    q->enabled = enabled;
    q->reset_scan = true;
}
static inline void input_push(MirrorInput *q, unsigned kind, unsigned flags, unsigned code, int x,
                              int y) {
    if (!q->enabled)
        return;
    if (q->count && kind == INPUT_MOVE) {
        MirrorEvent *tail = &q->events[(q->head + q->count - 1) % INPUT_QUEUE];
        int nx = tail->x + x, ny = tail->y + y;
        if (tail->kind == INPUT_MOVE && tail->sequence > q->sent && nx >= -32767 && nx <= 32767 &&
            ny >= -32767 && ny <= 32767) {
            tail->x = (int16_t)nx;
            tail->y = (int16_t)ny;
            return;
        }
    }
    /* Never drop a key-up and leave a host key held. Reset host state first. */
    if (q->count == INPUT_QUEUE || q->next >= 0xfffffffeU) {
        q->head = q->count = 0;
        ++q->overflows;
        if (q->next >= 0xfffffffeU) {
            q->enabled = false;
            return;
        }
        input_push(q, INPUT_RELEASE, 0, 0, 0, 0);
    }
    MirrorEvent *e = &q->events[(q->head + q->count++) % INPUT_QUEUE];
    *e = (MirrorEvent){++q->next,      (uint8_t)kind, (uint8_t)flags,
                       (uint16_t)code, (int16_t)x,    (int16_t)y};
}
static inline bool input_acknowledge(MirrorInput *q, uint32_t sequence) {
    if (sequence > q->sent)
        return false;
    if (sequence <= q->acknowledged)
        return true;
    q->acknowledged = sequence;
    while (q->count && q->events[q->head].sequence <= sequence) {
        q->head = (q->head + 1) % INPUT_QUEUE;
        --q->count;
    }
    return true;
}
static inline unsigned input_reply(MirrorInput *q, uint8_t *out) {
    if (!q->enabled)
        return 32;
    unsigned count = q->count < 17 ? q->count : 17;
    memcpy(out + 32, "NDE1", 4);
    wire_put(out + 36, count);
    for (unsigned i = 0; i < count; ++i) {
        const MirrorEvent *e = &q->events[(q->head + i) % INPUT_QUEUE];
        uint8_t *p = out + 40 + i * 12;
        wire_put(p, e->sequence);
        p[4] = e->kind;
        p[5] = e->flags;
        p[6] = (uint8_t)e->code;
        p[7] = (uint8_t)(e->code >> 8);
        p[8] = (uint8_t)e->x;
        p[9] = (uint8_t)((uint16_t)e->x >> 8);
        p[10] = (uint8_t)e->y;
        p[11] = (uint8_t)((uint16_t)e->y >> 8);
        if (e->sequence > q->sent)
            q->sent = e->sequence;
    }
    return 40 + count * 12;
}
/* Foreground only; no input SDK or allocator calls inside USB callbacks. */
void mirror_input_scan(MirrorInput *queue, uint32_t now, bool reserved);
#endif
