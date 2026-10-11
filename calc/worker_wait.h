#ifndef MIRROR_WORKER_WAIT_H
#define MIRROR_WORKER_WAIT_H
#include "portable_resolver.h"

#define MIRROR_WAIT_WORDS 128U
typedef struct {
    uint32_t task, state, stack_begin, stack_end, saved_sp;
    uint32_t cleanup, wait_info, count;
    uint32_t words[MIRROR_WAIT_WORDS];
} MirrorWorkerWait;

/* Read-only Nucleus task snapshot, with IRQ/FIQ excluded by the caller.
 * OS 6.2 TCT_Check_Stack uses +0x24/+0x28 for stack bounds and context switching
 * saves SP at +0x2c. Never wake a task, change a mutex or alter saved registers.
 * On any mismatch leave the output unchanged, including partial reads. */
static bool mirror_worker_wait(const PortableView *v, uint32_t task, MirrorWorkerWait *out) {
    if (!v || !out || !v->allow_span || !v->read_word || !task || (task & 3U) ||
        task < v->ram_begin || task > v->ram_end || v->ram_end - task < 0x70U ||
        !v->allow_span(v->context, task, 0x70U, PORTABLE_DATA))
        return false;
    MirrorWorkerWait snapshot = {0};
    uint32_t magic;
    if (!v->read_word(v->context, task + 0xcU, &magic) || magic != 0x5441534bU ||
        !v->read_word(v->context, task + 0x18U, &snapshot.state) ||
        !v->read_word(v->context, task + 0x24U, &snapshot.stack_begin) ||
        !v->read_word(v->context, task + 0x28U, &snapshot.stack_end) ||
        !v->read_word(v->context, task + 0x2cU, &snapshot.saved_sp) ||
        !v->read_word(v->context, task + 0x68U, &snapshot.cleanup) ||
        !v->read_word(v->context, task + 0x6cU, &snapshot.wait_info))
        return false;
    uint32_t begin = snapshot.stack_begin, end = snapshot.stack_end, sp = snapshot.saved_sp;
    if ((begin & 3U) || (end & 3U) || (sp & 3U) || begin < v->ram_begin ||
        end >= v->ram_end || end < begin || end - begin < 1024U ||
        end - begin > 512U * 1024U || sp < begin || sp > end || end - sp < 44U)
        return false;
    unsigned count = (end - sp) / 4U + 1U;
    if (count > MIRROR_WAIT_WORDS)
        count = MIRROR_WAIT_WORDS;
    if (!v->allow_span(v->context, sp, count * 4U, PORTABLE_DATA))
        return false;
    for (unsigned i = 0; i < count; ++i)
        if (!v->read_word(v->context, sp + i * 4U, &snapshot.words[i]))
            return false;
    snapshot.task = task;
    snapshot.count = count;
    *out = snapshot;
    return true;
}
#endif
