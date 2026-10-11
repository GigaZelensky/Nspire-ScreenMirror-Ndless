#ifndef MIRROR_IDLE_TIMING_H
#define MIRROR_IDLE_TIMING_H
#include <stdint.h>
#include <stdbool.h>
#ifndef MIRROR_IDLE_TIMING
#define MIRROR_IDLE_TIMING 0
#endif
#define MIRROR_IDLE_BASELINE_MHZ 396U
#define MIRROR_IDLE_ENTRY 0xa4001890U
#define MIRROR_IDLE_LITERAL 0xa40019e0U
#define MIRROR_IDLE_ORIGINAL 0x1d1e92e9U
#define MIRROR_IDLE_HELD 0xfd7d1cf3U
typedef struct {
    void *context;
    bool (*read)(void *, uint32_t, uint32_t *);
    bool (*write)(void *, uint32_t, uint32_t);
} MirrorTimingAccess;
typedef struct {
    bool supported, held;
    unsigned holds, restores, revocations;
    int status;
} MirrorIdleTiming;
static inline bool mirror_timing_hash(const MirrorTimingAccess *io, uint32_t *hash) {
    *hash = 2166136261U;
    for (unsigned i = 0; i < 86U; ++i) {
        uint32_t word;
        if (!io->read(io->context, MIRROR_IDLE_ENTRY + i * 4U, &word)) return false;
        *hash = (*hash ^ word) * 16777619U;
    }
    return true;
}
static inline bool mirror_timing_resolve(const MirrorTimingAccess *io, uint32_t asic,
                                         uint32_t mapping) {
    uint32_t pointer, hash;
    /* Exact native identity mapping, uncached, privileged RW. Buffered stores
     * are accepted only through the caller's write-buffer drain/readback. */
    return asic == 0x202U && (mapping & 0xfff00c07U) == 0xa4000c02U &&
           io->read(io->context, 0x10c52e3cU, &pointer) && pointer == MIRROR_IDLE_ENTRY &&
           mirror_timing_hash(io, &hash) && hash == MIRROR_IDLE_ORIGINAL;
}
/* Caller masks IRQ/FIQ. Only the witnessed SRAM literal is written. The
 * native low-clock/WFI literal, instructions and OS policy remain untouched. */
static inline bool mirror_timing_hold(MirrorIdleTiming *p, const MirrorTimingAccess *io) {
    uint32_t hash;
    if (!p->supported || !mirror_timing_hash(io, &hash)) {p->status = -1; return false;}
    if (p->held && hash == MIRROR_IDLE_HELD) return true;
    if (hash != MIRROR_IDLE_ORIGINAL) {p->status = -2; return false;}
    if (p->held) ++p->revocations; /* A native reinitialization restored its image. */
    p->held = true;
    ++p->holds;
    if (!io->write(io->context, MIRROR_IDLE_LITERAL, 0x528U) ||
        !mirror_timing_hash(io, &hash) || hash != MIRROR_IDLE_HELD) {
        p->status = -3;
        return false;
    }
    p->status = 0;
    return true;
}
static inline bool mirror_timing_release(MirrorIdleTiming *p, const MirrorTimingAccess *io) {
    if (!p->held) return true;
    uint32_t hash;
    if (!mirror_timing_hash(io, &hash)) {p->status = -4; return false;}
    if (hash == MIRROR_IDLE_ORIGINAL) {
        p->held = false;
        ++p->restores;
        p->status = 0;
        return true;
    }
    if (hash != MIRROR_IDLE_HELD) {
        ++p->revocations; /* Preserve an unrelated native code change. */
        p->held = false;
        p->status = -5;
        return false;
    }
    if (!io->write(io->context, MIRROR_IDLE_LITERAL, 0x502U) ||
        !mirror_timing_hash(io, &hash) || hash != MIRROR_IDLE_ORIGINAL) {
        p->status = -6;
        return false;
    }
    p->held = false;
    ++p->restores;
    p->status = 0;
    return true;
}
static inline bool mirror_timing_after_clock(MirrorIdleTiming *p, const MirrorTimingAccess *io,
                                             bool overclock, bool ok, bool held_before,
                                             unsigned actual_mhz) {
    if ((ok && !overclock) || (!ok && !held_before && actual_mhz &&
                              actual_mhz <= MIRROR_IDLE_BASELINE_MHZ))
        return mirror_timing_release(p, io);
    return true;
}
#endif
