#ifndef MIRROR_NNSE_ABORT_H
#define MIRROR_NNSE_ABORT_H
#include "portable_resolver.h"

static bool mirror_nnse_hash(const PortableView *v, uint32_t entry, unsigned words,
                             uint32_t *result) {
    if (entry < v->code_begin || entry > v->code_end || v->code_end - entry < words * 4U ||
        !v->allow_span(v->context, entry, words * 4U, PORTABLE_CODE))
        return false;
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < words; ++i) {
        uint32_t word;
        if (!v->read_word(v->context, entry + i * 4U, &word))
            return false;
        hash = (hash ^ word) * 16777619U;
    }
    *result = hash;
    return true;
}
/* OS 6.2 CX II profile: native connector state-change handler and its NNSE
 * disconnect cleanup, including complete instruction/literal witnesses.
 * Invoke in place with connected=0; never copy/patch either function.
 * The full handler first marks the connector disconnected, aborts receive and
 * transmit queues, then notifies the stack. Calling only its NNSE helper would
 * omit the state transition required to stop the native receive listener. */
static uint32_t mirror_nnse_abort_target(const PortableView *v, uint32_t asic,
                                         uint32_t write_export, uint32_t task_export) {
    const uint32_t entry = 0x10616aa8U;
    uint32_t connector_hash, abort_hash;
    if (!v || !v->allow_span || !v->read_word || asic != 0x202U || write_export != 0x100bb1a4U ||
        task_export != 0x10429200U || !mirror_nnse_hash(v, entry, 67U, &connector_hash) ||
        connector_hash != 0xba6faf49U || !mirror_nnse_hash(v, 0x1061a254U, 57U, &abort_hash) ||
        abort_hash != 0x349b2031U)
        return 0;
    if (!v->allow_span(v->context, 0x113e1bb4U, 4U, PORTABLE_DATA))
        return 0;
    /* Service startup can initialize WS mode after this code lookup. Its live
     * value is an invocation gate, not a reason to disable the code forever. */
    return entry;
}

/* FOTG210 B-device session state: require a live readable controller and an
 * ended session, not merely a zero/gated register. Register definitions:
 * U-Boot include/usb/fotg210.h (OTGCSR B_SESS_VLD/B_SESS_END/ROLE_D/DEV_B).
 * The CX II register base/layout is verified by the existing ASIC adapter. */
static bool mirror_nnse_detached(uint32_t asic, uint32_t capability, uint32_t otg) {
    const uint32_t device_role = (1U << 21) | (1U << 20);
    return asic == 0x202U && (capability & 0xffff00ffU) == 0x01000010U && otg != UINT32_MAX &&
           (otg & device_role) == device_role && !(otg & (1U << 17)) && (otg & (1U << 16));
}

/* Explicit app exit also needs a logical disconnect when the cable remains
 * connected: the dispatch wrapper may still hold resources after callbacks=0. */
static bool mirror_nnse_shutdown_link(uint32_t asic, uint32_t capability, uint32_t otg,
                                      bool closing) {
    const uint32_t device_role = (1U << 21) | (1U << 20);
    return closing &&
           (mirror_nnse_detached(asic, capability, otg) ||
            (asic == 0x202U && (capability & 0xffff00ffU) == 0x01000010U && otg != UINT32_MAX &&
             (otg & device_role) == device_role && (otg & (1U << 17))));
}
#endif
