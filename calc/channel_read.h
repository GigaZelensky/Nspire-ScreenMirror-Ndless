#ifndef MIRROR_CHANNEL_READ_H
#define MIRROR_CHANNEL_READ_H
#include "portable_resolver.h"

/* Recognize the complete channel-read body before bypassing the public
 * wrapper's 62-second minimum. Only address-dependent calls/loads vary. */
static bool mirror_channel_body(const PortableView *v, uint32_t address) {
    if (!address || (address & 3U) || address < v->code_begin ||
        address > v->code_end || v->code_end - address < 280U ||
        !v->allow_span(v->context, address, 280U, PORTABLE_CODE))
        return false;
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < 70U; ++i) {
        uint32_t op;
        if (!v->read_word(v->context, address + 4U * i, &op))
            return false;
        if ((op & 0x0f000000U) == 0x0b000000U)
            op &= 0xff000000U; /* BL to relocated native helpers */
        else if ((op & 0x0e5f0000U) == 0x041f0000U)
            op &= 0xfffff000U; /* PC-relative literal position */
        hash = (hash ^ op) * 16777619U;
    }
    return hash == 0x820dc501U;
}
static uint32_t mirror_channel_target(const PortableView *v, uint32_t address) {
    if (!v || !v->allow_span || !v->read_word)
        return 0;
    if (mirror_channel_body(v, address))
        return address;
    if (!address || (address & 3U) || address < v->code_begin ||
        address > v->code_end || v->code_end - address < 40U ||
        !v->allow_span(v->context, address, 40U, PORTABLE_CODE))
        return 0;
    uint32_t w[10];
    for (unsigned i = 0; i < 10U; ++i)
        if (!v->read_word(v->context, address + 4U * i, &w[i]))
            return 0;
    if (w[0] != 0xe92d0030U || w[1] != 0xe59f4014U || w[2] != 0xe59fc014U ||
        w[3] != 0xe2415001U || w[4] != 0xe1550004U || w[5] != 0x91a0100cU ||
        w[6] != 0xe8bd0030U || (w[7] & 0xff000000U) != 0xea000000U ||
        w[8] != 61998U || w[9] != 62000U)
        return 0;
    int32_t displacement = (int32_t)(w[7] << 8) >> 6;
    uint32_t target = address + 36U + displacement;
    return mirror_channel_body(v, target) ? target : 0;
}
#endif
