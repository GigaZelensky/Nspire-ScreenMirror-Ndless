#ifndef NDMIRROR_CACHE_PLAN_H
#define NDMIRROR_CACHE_PLAN_H
#include <stdbool.h>
#include <stdint.h>
#define COLOR_BYTES 5504U
#define SRAM_TABLE_ADDRESS 0xa4020000U
/* This first experiment targets the measured 8 KiB, four-way, 32-byte cache.
 * Other geometries must report unsupported rather than use this fill plan. */
static inline bool cache_plan_supported(uint32_t type, uint32_t control, uint32_t lockdown) {
    return ((type >> 18) & 15U) == 4U && ((type >> 15) & 7U) == 2U && ((type >> 12) & 3U) == 2U &&
           !(type & (1U << 14)) && (control & 5U) == 5U && !(lockdown & 15U);
}
static inline bool sram_stack_clear(uint32_t sp) {
    return sp < SRAM_TABLE_ADDRESS - 8192U || sp >= SRAM_TABLE_ADDRESS + COLOR_BYTES + 8192U;
}
static inline bool sram_plan_supported(uint32_t asic, uint32_t ttbr, uint32_t descriptor,
                                       bool usb_idle, bool dma_idle) {
    return asic == 0x202U && ttbr == 0xa4004000U && descriptor == 0xa4000c1aU && usb_idle &&
           dma_idle;
}
#endif
