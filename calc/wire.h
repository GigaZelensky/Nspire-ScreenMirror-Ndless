#ifndef NDMIRROR_WIRE_H
#define NDMIRROR_WIRE_H
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#ifndef MIRROR_SRAM_TABLES
#define MIRROR_SRAM_TABLES 0
#endif
#ifndef MIRROR_CAPTURE_INPUT
#define MIRROR_CAPTURE_INPUT 0
#endif
#define MIRROR_SERVICE 0x4008U
#define MIRROR_HEADER 32U
#define MIRROR_PACKET 1440U
#define MIRROR_H264_FRAME (64U * 1024U)
#define MIRROR_FRAME (160U * 1024U)
#define MIRROR_SLOTS 2U
enum {
    MIRROR_HELLO = 1,
    MIRROR_FRAME_DATA = 2,
    MIRROR_POLL = 3,
    MIRROR_RESET = 4,
    MIRROR_CAPS = 5,
    MIRROR_PROBE = 6,
    MIRROR_CLOCK = 7,
    MIRROR_INPUT = 8,
    MIRROR_STATS = 9,
    MIRROR_STREAM = 10,
    MIRROR_PAUSE = 11,
    MIRROR_DISPLAY_RATE = 12
};
enum { MIRROR_OK = 0, MIRROR_BUSY = 1, MIRROR_BAD = 2, MIRROR_RESYNC = 3, MIRROR_CLOSING = 4 };
enum {
    MIRROR_FIRST = 1,
    MIRROR_LAST = 2,
    MIRROR_KEY = 4,
    MIRROR_BULK = 8,
    MIRROR_RAW = 16,
    MIRROR_DELTA = 32,
    MIRROR_LZ4 = 64,
    MIRROR_MPEG4 = 128
};
enum {
    MIRROR_CAP_BULK = 1,
    MIRROR_CAP_PROBE = 2,
    MIRROR_CAP_RAW = 4,
    MIRROR_CAP_PIXELS = 8,
    MIRROR_CAP_CLOCK = 16,
    MIRROR_CAP_INPUT = 32,
    MIRROR_CAP_MPEG4 = 64,
    MIRROR_CAP_CX2 = 256,
    MIRROR_CAP_STATS = 512,
    MIRROR_CAP_STREAM = 1024,
    MIRROR_CAP_PAUSE = 2048,
    MIRROR_CAP_SRAM_TABLES = 4096,
    MIRROR_CAP_DISPLAY_RATE = 8192,
    MIRROR_CAP_PAUSE_PEER_CLOSE = 16384
};
static inline uint32_t wire_get(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static inline void wire_put(uint8_t *p, uint32_t v) {
    for (unsigned i = 0; i < 4; ++i)
        p[i] = (uint8_t)(v >> (8 * i));
}
typedef struct {
    uint32_t session, frame, total, offset;
    uint16_t bytes;
    uint8_t kind, flags;
    const uint8_t *data;
    uint32_t input_ack;
} MirrorPacket;
static inline bool mirror_parse(const uint8_t *p, unsigned n, unsigned limit, MirrorPacket *out) {
    if (n < MIRROR_HEADER || n > limit || memcmp(p, "NDM1", 4) || p[4] != 1 || p[7] ||
        p[5] < MIRROR_HELLO || p[5] > MIRROR_DISPLAY_RATE)
        return false;
    uint32_t session = wire_get(p + 8), id = wire_get(p + 12), total = wire_get(p + 16),
             offset = wire_get(p + 20);
    unsigned bytes = p[24] | (unsigned)p[25] << 8;
    if (!session || p[26] || p[27] || n != MIRROR_HEADER + bytes)
        return false;
    if ((p[5] == MIRROR_HELLO || p[5] == MIRROR_RESET || p[5] == MIRROR_INPUT) && wire_get(p + 28))
        return false;
    if (p[5] != MIRROR_FRAME_DATA && p[5] != MIRROR_PROBE) {
        if (bytes || total || offset || p[6] ||
            (p[5] != MIRROR_CLOCK && p[5] != MIRROR_INPUT && p[5] != MIRROR_STATS &&
             p[5] != MIRROR_DISPLAY_RATE && id) ||
            (p[5] == MIRROR_DISPLAY_RATE && id > 1000U) || (p[5] == MIRROR_INPUT && id > 1) ||
            (p[5] == MIRROR_STATS && id > (MIRROR_SRAM_TABLES ? 4U : 2U)))
            return false;
    } else {
        if (!id || !bytes || !total || total > MIRROR_FRAME || offset > total ||
            bytes > total - offset || ((p[6] & MIRROR_FIRST) != 0) != (offset == 0) ||
            ((p[6] & MIRROR_LAST) != 0) != (offset + bytes == total))
            return false;
        unsigned codec = p[6] & (MIRROR_RAW | MIRROR_DELTA);
        if (codec == (MIRROR_RAW | MIRROR_DELTA) || (p[6] & MIRROR_LZ4 && !codec))
            return false;
        if ((p[6] & MIRROR_MPEG4) && (codec || (p[6] & MIRROR_LZ4)))
            return false;
        if (p[5] == MIRROR_PROBE && (p[6] & ~(MIRROR_FIRST | MIRROR_LAST | MIRROR_BULK)))
            return false;
        if (p[5] == MIRROR_FRAME_DATA && !codec && total > MIRROR_H264_FRAME)
            return false;
        if (codec == MIRROR_RAW && !(p[6] & MIRROR_LZ4) && total != 320U * 240U * 2U)
            return false;
    }
    *out = (MirrorPacket){session,         id,   total, offset,
                          (uint16_t)bytes, p[5], p[6],  p + MIRROR_HEADER,
                          wire_get(p + 28)};
    return true;
}
typedef struct {
    uint8_t data[MIRROR_FRAME + 16];
    uint32_t id, total, used;
    uint8_t flags;
    bool key, ready, busy;
} MirrorFrame;
typedef struct {
    MirrorFrame frame[MIRROR_SLOTS];
    uint32_t session, last_enqueued, displayed, received, rejected;
    int assembling;
    unsigned queued;
    bool reset, need_key, paused;
} MirrorQueue;
static inline void mirror_queue_init(MirrorQueue *q) {
    memset(q, 0, sizeof(*q));
    q->assembling = -1;
    q->need_key = true;
}
/* Main calls this after a decode error with IRQ exclusion. Preserve the busy
 * frame until its caller releases it, but never decode its queued dependents
 * through a failed decoder. The next keyframe starts from a reset decoder. */
static inline void mirror_queue_recover(MirrorQueue *q) {
    for (unsigned i = 0; i < MIRROR_SLOTS; ++i)
        q->frame[i].ready = false;
    q->assembling = -1;
    q->queued = 0;
    q->need_key = true;
    q->reset = true;
}
/* Caller owns IRQ exclusion. No decoder, allocator, LCD or file calls here. */
static inline unsigned mirror_accept(MirrorQueue *q, const MirrorPacket *p) {
    if (p->kind == MIRROR_HELLO || p->kind == MIRROR_RESET) {
        for (unsigned i = 0; i < MIRROR_SLOTS; ++i)
            if (q->frame[i].busy)
                return MIRROR_BUSY;
        mirror_queue_init(q);
        q->session = p->session;
        q->reset = true;
        return MIRROR_OK;
    }
    if (p->session != q->session)
        return MIRROR_RESYNC;
    if (p->kind == MIRROR_PAUSE) {
        mirror_queue_recover(q);
        q->paused = true;
        return MIRROR_OK;
    }
    if (p->kind == MIRROR_CAPS || p->kind == MIRROR_PROBE || p->kind == MIRROR_CLOCK ||
        p->kind == MIRROR_INPUT || p->kind == MIRROR_STATS || p->kind == MIRROR_STREAM ||
        p->kind == MIRROR_DISPLAY_RATE)
        return MIRROR_OK;
    if (p->kind == MIRROR_POLL)
        return q->need_key ? MIRROR_RESYNC : MIRROR_OK;
    if (q->paused || (q->need_key && !(p->flags & MIRROR_KEY)))
        return MIRROR_RESYNC;
    if (p->flags & MIRROR_FIRST) {
        if (q->assembling >= 0)
            return MIRROR_BAD;
        if (q->need_key && !(p->flags & MIRROR_KEY))
            return MIRROR_RESYNC;
        if (q->last_enqueued && p->frame != q->last_enqueued + 1U)
            return MIRROR_RESYNC;
        unsigned i;
        for (i = 0; i < MIRROR_SLOTS; ++i)
            if (!q->frame[i].ready && !q->frame[i].busy)
                break;
        if (i == MIRROR_SLOTS)
            return MIRROR_BUSY;
        q->assembling = (int)i;
        q->frame[i].id = p->frame;
        q->frame[i].total = p->total;
        q->frame[i].used = 0;
        q->frame[i].flags = p->flags & ~(MIRROR_FIRST | MIRROR_LAST);
        q->frame[i].key = (p->flags & MIRROR_KEY) != 0;
    }
    if (q->assembling < 0)
        return MIRROR_BAD;
    MirrorFrame *f = &q->frame[q->assembling];
    if (f->id != p->frame || f->total != p->total || f->used != p->offset ||
        f->flags != (p->flags & ~(MIRROR_FIRST | MIRROR_LAST))) {
        q->assembling = -1;
        ++q->rejected;
        q->need_key = true;
        return MIRROR_BAD;
    }
    memcpy(f->data + f->used, p->data, p->bytes);
    f->used += p->bytes;
    if (p->flags & MIRROR_LAST) {
        memset(f->data + f->used, 0, 16);
        f->ready = true;
        ++q->queued;
        q->last_enqueued = f->id;
        q->assembling = -1;
        q->need_key = false;
        ++q->received;
    }
    return MIRROR_OK;
}
static inline int mirror_take(MirrorQueue *q) {
    int selected = -1;
    for (unsigned i = 0; i < MIRROR_SLOTS; ++i)
        if (q->frame[i].ready && !q->frame[i].busy &&
            (selected < 0 || (int32_t)(q->frame[i].id - q->frame[selected].id) < 0))
            selected = (int)i;
    if (selected >= 0) {
        q->frame[selected].ready = false;
        q->frame[selected].busy = true;
        --q->queued;
    }
    return selected;
}
static inline unsigned mirror_credit(const MirrorQueue *q) {
    unsigned free = 0;
    for (unsigned i = 0; i < MIRROR_SLOTS; ++i)
        if (!q->frame[i].ready && !q->frame[i].busy && (int)i != q->assembling)
            ++free;
    return free;
}
static inline void mirror_reply(uint8_t out[32], const MirrorQueue *q, unsigned status,
                                unsigned limit) {
    memset(out, 0, 32);
    memcpy(out, "NDA1", 4);
    out[4] = 1;
    out[5] = (uint8_t)status;
    out[6] = (uint8_t)mirror_credit(q);
    out[7] = (uint8_t)q->need_key;
    wire_put(out + 8, q->session);
    wire_put(out + 12, q->last_enqueued);
    wire_put(out + 16, q->displayed);
    wire_put(out + 20, limit);
    wire_put(out + 24, q->received);
    wire_put(out + 28, q->rejected);
}
#endif
