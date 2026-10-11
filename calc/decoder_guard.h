#ifndef NDMIRROR_DECODER_GUARD_H
#define NDMIRROR_DECODER_GUARD_H
#include "decoder.h"
#include "wire.h"
#ifndef MIRROR_DECODE_GUARD
#define MIRROR_DECODE_GUARD 0
#endif
#if MIRROR_DECODE_GUARD
/* Diagnostic only. These allocations belong exclusively to the suspended
 * decoder. Save addresses before yielding: never follow a changed pointer
 * when comparing afterwards. Input is already unescaped by h264bsd here. */
enum { MIRROR_GUARD_REGIONS = 8 };
typedef struct {
    const void *address;
    unsigned bytes;
    uint32_t before, after;
} MirrorGuardRegion;
typedef struct {
    MirrorGuardRegion region[MIRROR_GUARD_REGIONS];
    uint32_t checks, changes, frame, mb, bit, mask;
    bool armed;
} MirrorDecodeGuard;
static inline uint32_t mirror_guard_hash(const void *address, unsigned bytes) {
    const volatile uint8_t *p = (const volatile uint8_t *)address;
    uint32_t hash = 2166136261U;
    while (bytes && ((uintptr_t)p & 3U)) {
        hash = (hash ^ *p++) * 16777619U;
        --bytes;
    }
    while (bytes >= 4U) {
        hash = (hash ^ *(const volatile uint32_t *)(const void *)p) * 16777619U;
        p += 4;
        bytes -= 4;
    }
    while (bytes--)
        hash = (hash ^ *p++) * 16777619U;
    return hash;
}
static inline void mirror_guard_region(MirrorGuardRegion *r, const void *p, unsigned bytes) {
    *r = (MirrorGuardRegion){p, bytes, mirror_guard_hash(p, bytes), 0};
}
static inline bool mirror_guard_begin(MirrorDecodeGuard *g, const MirrorDecoder *d,
                                      const MirrorFrame *f) {
    g->armed = false;
    const storage_t *s = d->storage;
    if (!s || !s->slice->decodeInProgress || s->picSizeInMbs != 300U || !s->mb ||
        !s->mbLayer || !s->sliceGroupMap || !s->activeSps || !s->activePps ||
        f->total > MIRROR_H264_FRAME)
        return false;
    g->frame = f->id;
    g->mb = s->slice->currMbAddr;
    g->bit = s->strm->strmBuffReadBits;
    mirror_guard_region(&g->region[0], f->data, f->total + 16U);
    mirror_guard_region(&g->region[1], d, sizeof(*d));
    mirror_guard_region(&g->region[2], s, sizeof(*s));
    mirror_guard_region(&g->region[3], s->mb, 300U * sizeof(*s->mb));
    mirror_guard_region(&g->region[4], s->mbLayer, sizeof(*s->mbLayer));
    mirror_guard_region(&g->region[5], s->sliceGroupMap, 300U * sizeof(*s->sliceGroupMap));
    mirror_guard_region(&g->region[6], s->activeSps, sizeof(*s->activeSps));
    mirror_guard_region(&g->region[7], s->activePps, sizeof(*s->activePps));
    g->armed = true;
    return true;
}
static inline unsigned mirror_guard_end(MirrorDecodeGuard *g) {
    if (!g->armed)
        return 0;
    g->armed = false;
    g->mask = 0;
    ++g->checks;
    for (unsigned i = 0; i < MIRROR_GUARD_REGIONS; ++i) {
        MirrorGuardRegion *r = &g->region[i];
        r->after = mirror_guard_hash(r->address, r->bytes);
        if (r->before != r->after)
            g->mask |= 1U << i;
    }
    if (g->mask)
        ++g->changes;
    return g->mask;
}
#endif
#endif
