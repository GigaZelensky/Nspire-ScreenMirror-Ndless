#ifndef MIRROR_SRAM_TABLE_GATE_H
#define MIRROR_SRAM_TABLE_GATE_H
#include <stdbool.h>
#include <stdint.h>
#define MIRROR_COLOR_BYTES 5504U
#define MIRROR_TABLE_SRAM 0xa4020000U
enum {
    TABLE_RAM_OFF,
    TABLE_SRAM_USED,
    TABLE_LAYOUT,
    TABLE_STACK,
    TABLE_USB_BUSY,
    TABLE_DMA_BUSY,
    TABLE_RESTORE_ERROR
};
static inline bool mirror_table_stack_clear(uint32_t sp) {
    return sp < MIRROR_TABLE_SRAM - 8192U || sp >= MIRROR_TABLE_SRAM + MIRROR_COLOR_BYTES + 8192U;
}
static inline unsigned mirror_table_gate(uint32_t asic, uint32_t table, uint32_t descriptor,
                                         uint32_t control, bool stacks_clear, uint32_t host0,
                                         uint32_t host1, uint32_t usb_dma0, uint32_t usb_dma1,
                                         bool dma_layout, uint32_t dma_busy) {
    if (asic != 0x202U || table != 0xa4004000U || descriptor != 0xa4000c1aU || !(control & 1U) ||
        !dma_layout)
        return TABLE_LAYOUT;
    if (!stacks_clear)
        return TABLE_STACK;
    if ((host0 | host1 | usb_dma0 | usb_dma1) & 1U)
        return TABLE_USB_BUSY;
    if (dma_busy)
        return TABLE_DMA_BUSY;
    return TABLE_SRAM_USED;
}
#endif
