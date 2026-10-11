#ifndef NDVIDEO_ARM926_RAM_SPAN_H
#define NDVIDEO_ARM926_RAM_SPAN_H

#include <stdbool.h>
#include <stdint.h>

/* ARM926EJ-S ARMv5 short descriptors, not ARMv6 extended/XN descriptors.
 * The caller must authenticate the active L1 table and its readable alias,
 * and prevent mappings changing for the duration of this check/use. The
 * verified OS uses accessible domain 0 (native DACR=3); other domains are
 * deliberately unsupported, including for coarse-table storage.
 * Accept only identity-mapped 64 MiB DRAM with explicit privileged R/W AP.
 * This is deliberately conservative: AP0 is rejected even when domain-manager
 * or S/R settings would allow it. Coarse tables need identity SECTION backing;
 * fine/tiny pages and recursively paged table storage are unsupported.
 * Descriptor layout: ARM DDI 0198E, sections 3.2.5 and 3.2.8,
 * figures 3-6 and 3-9. */
typedef uint32_t (*Arm926ReadWord)(uint32_t address, void *context);
typedef struct {
    uint32_t address, descriptor;
    bool have_descriptor;
} Arm926RamSpanFailure;

static inline bool arm926_span_reject(Arm926RamSpanFailure *failure, uint32_t address,
                                      uint32_t descriptor, bool have_descriptor)
{
    if (failure)
        *failure = (Arm926RamSpanFailure){address, descriptor, have_descriptor};
    return false;
}
static inline bool arm926_ram_span(uint32_t address, uint32_t bytes, uint32_t l1_alias,
                                   Arm926ReadWord read_word, void *context,
                                   Arm926RamSpanFailure *failure)
{
    if (failure)
        *failure = (Arm926RamSpanFailure){0, 0, false};
    if (!read_word || (address & 3U) || !bytes || bytes > 0x4000000U || address < 0x10000000U ||
        address > 0x14000000U - bytes)
        return arm926_span_reject(failure, address, 0, false);
    const uint32_t limit = address + bytes;
    while (address < limit) {
        /* This exact-firmware adapter accepts identity DRAM only. A valid
         * table descriptor alone must never authorize a dereference of MMIO
         * or a different physical alias. The canonical L1 alias is verified
         * by the caller before arriving here. */
        const uint32_t section = address & 0xFFF00000U;
        const uint32_t section_end = section + 0x100000U;
        const uint32_t end = limit < section_end ? limit : section_end;
        uint32_t entry = read_word(l1_alias + (address >> 20) * 4U, context);
        if (entry & 0x1E0U)
            return arm926_span_reject(failure, address, entry, true);
        if ((entry & 3U) == 2U) {
            if ((entry & 0xFFF00000U) != section || !(entry & 0xC00U))
                return arm926_span_reject(failure, address, entry, true);
            address = end;
            continue;
        }
        if ((entry & 3U) != 1U)
            return arm926_span_reject(failure, address, entry, true);
        const uint32_t table = entry & 0xFFFFFC00U;
        if (table < 0x10000000U || table > 0x13FFFC00U)
            return arm926_span_reject(failure, address, entry, true);
        /* ARM926 coarse tables are 1 KiB aligned/sized, hence cannot cross
         * this section. Prove the entire backing section is writable,
         * identity DRAM before reading any L2 word. Do not recursively trust
         * another paged mapping to find a physical page-table address. */
        uint32_t backing = read_word(l1_alias + (table >> 20) * 4U, context);
        if ((backing & 0x1E3U) != 2U || (backing & 0xFFF00000U) != (table & 0xFFF00000U) ||
            !(backing & 0xC00U))
            return arm926_span_reject(failure, table, backing, true);
        while (address < end) {
            const uint32_t index = (address >> 12) & 255U;
            uint32_t page = read_word(table + index * 4U, context);
            uint32_t page_mask, subpage_shift;
            if ((page & 3U) == 1U) {
                /* ARMv5 large pages are 64 KiB, with four 16 KiB AP
                 * subpages and sixteen identical coarse-table entries.
                 * Bits 15:12 are SBZ, not an extended physical address. */
                if (page & 0xF000U)
                    return arm926_span_reject(failure, address, page, true);
                for (uint32_t i = index & ~15U; i < (index & ~15U) + 16U; ++i) {
                    uint32_t repeated = read_word(table + i * 4U, context);
                    if (repeated != page)
                        return arm926_span_reject(failure, section + (i << 12), repeated, true);
                }
                page_mask = 0xFFFF0000U;
                subpage_shift = 14U;
            } else if ((page & 3U) == 2U) {
                /* ARMv5 small pages have a distinct AP for each 1 KiB.
                 * Type 3 is not an ARMv6 small-page XN variant here. */
                page_mask = 0xFFFFF000U;
                subpage_shift = 10U;
            } else
                return arm926_span_reject(failure, address, page, true);
            if ((page & page_mask) != (address & page_mask))
                return arm926_span_reject(failure, address, page, true);
            uint32_t page_end = (address & page_mask) + (~page_mask + 1U);
            if (page_end > end)
                page_end = end;
            const uint32_t subpage_bytes = 1U << subpage_shift;
            do {
                const uint32_t permission_shift = 4U + 2U * ((address >> subpage_shift) & 3U);
                if (!((page >> permission_shift) & 3U))
                    return arm926_span_reject(failure, address, page, true);
                uint32_t next = (address & ~(subpage_bytes - 1U)) + subpage_bytes;
                address = next < page_end ? next : page_end;
            } while (address < page_end);
        }
    }
    return true;
}

#endif
