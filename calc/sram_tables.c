#include "sram_tables.h"
#if MIRROR_SRAM_TABLES && defined(__arm__)
#include "sram_table_gate.h"
#include "native_interrupts.h"
#include "nspire_hardware.h"
#include <string.h>
#define REG(a) (*(const volatile uint32_t *)(uintptr_t)(a))
static struct {
    bool enabled, borrowed;
    unsigned entry_mask, phase, reason, timer_errors, restore_errors;
    uint32_t frames[2], skips[7], timer, period, hz, sample, host[2], usb_dma[2], dma_busy;
    uint64_t convert[2], setup, restore, verify;
} state;
static uint32_t backup[MIRROR_COLOR_BYTES / 4U] __attribute__((aligned(32)));
static __attribute__((naked, noinline)) uint32_t banked_sp(unsigned mode __attribute__((unused))) {
    __asm__ volatile("mrs r1,cpsr\nbic r2,r1,#31\norr r2,r2,r0\nmsr cpsr_c,r2\n"
                     "mov r0,sp\nmsr cpsr_c,r1\nbx lr\n");
}
static void drain(void) {
    uint32_t zero = 0;
    __asm__ volatile("mcr p15,0,%0,c7,c10,4" ::"r"(zero) : "memory");
}
static void copy(volatile uint32_t *to, const volatile uint32_t *from) {
    unsigned chunks = MIRROR_COLOR_BYTES / 32U;
    __asm__ volatile("1: ldmia %1!, {r4-r11}\n\tstmia %0!, {r4-r11}\n\t"
                     "subs %2, %2, #1\n\tbne 1b"
                     : "+&r"(to), "+&r"(from), "+&r"(chunks)
                     :
                     : "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "cc", "memory");
    drain();
}
static uint32_t tick(void) { return state.timer ? REG(state.timer + 4U) : 0; }
static uint32_t elapsed(uint32_t before) {
    if (!state.timer)
        return 0;
    uint32_t after = tick();
    if (before >= state.period || after >= state.period) {
        ++state.timer_errors;
        return 0;
    }
    return before >= after ? before - after : before + state.period - after;
}
void mirror_tables_init(uint32_t os_hz) {
    memset(&state, 0, sizeof(state));
    state.enabled = true;
    static const uint32_t bases[] = {0x900c0000U, 0x900c0020U, 0x900d0000U, 0x900d0020U};
    for (unsigned i = 0; i < 4U; ++i) {
        uint32_t control = REG(bases[i] + 8U), load = REG(bases[i]);
        if ((control & 0xe2U) == 0xe2U && load && load < UINT32_MAX &&
            (uint64_t)(load + 1U) * os_hz <= UINT32_MAX) {
            state.timer = bases[i];
            state.period = load + 1U;
            state.hz = state.period * os_hz;
            break;
        }
    }
}
void mirror_tables_enable(bool enabled) {
    if (state.enabled != enabled)
        ++state.phase;
    state.enabled = enabled;
}
static unsigned gate(void) {
    uint32_t table, control;
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(table));
    table &= 0xffffc000U;
    __asm__ volatile("mrc p15,0,%0,c1,c0,0" : "=r"(control));
    if (!nspire_is_cx2() || table != 0xa4004000U)
        return TABLE_LAYOUT;
    bool stacks_clear = true;
    static const unsigned modes[] = {0x1fU, 0x12U, 0x11U, 0x17U, 0x1bU, 0x13U};
    for (unsigned i = 0; i < 6U; ++i)
        if (!mirror_table_stack_clear(banked_sp(modes[i])))
            stacks_clear = false;
    state.host[0] = REG(0xb0000010U);
    state.host[1] = REG(0xb4000010U);
    state.usb_dma[0] = REG(0xb00001c8U);
    state.usb_dma[1] = REG(0xb40001c8U);
    bool dma_layout = REG(0xbc000030U) == 0x11900U && REG(0xbc000034U) == 0x6303U;
    state.dma_busy = 0;
    if (dma_layout)
        for (unsigned ch = 0; ch < 6U; ++ch)
            if ((REG(0xbc000100U + ch * 32U) & 1U) || (REG(0xbc000104U + ch * 32U) & 0x100U))
                state.dma_busy |= 1U << ch;
    return mirror_table_gate(nspire_hardware_id(), table, REG(table + 0xa40U * 4U), control,
                             stacks_clear, state.host[0], state.host[1], state.usb_dma[0],
                             state.usb_dma[1], dma_layout, state.dma_busy);
}
const int32_t *mirror_tables_begin(const int32_t *original) {
    state.entry_mask = native_critical_enter();
    uint32_t start = tick();
    state.reason = state.enabled ? gate() : TABLE_RAM_OFF;
    state.borrowed = state.reason == TABLE_SRAM_USED;
    if (!state.borrowed) {
        ++state.skips[state.reason];
        return original;
    }
    /* No native call or yield is permitted until mirror_tables_end restores
     * these bytes. Idle USB DMA cannot be started by a masked native handler. */
    drain();
    copy(backup, (volatile uint32_t *)MIRROR_TABLE_SRAM);
    copy((volatile uint32_t *)MIRROR_TABLE_SRAM, (const uint32_t *)original);
    state.setup += elapsed(start);
    return (const int32_t *)MIRROR_TABLE_SRAM;
}
void mirror_tables_sample_begin(void) { state.sample = tick(); }
void mirror_tables_sample_end(void) {
    drain();
    state.convert[state.borrowed] += elapsed(state.sample);
}
static bool restored(void) {
    const volatile uint32_t *actual = (const volatile uint32_t *)MIRROR_TABLE_SRAM;
    const uint32_t *saved = backup;
    unsigned chunks = MIRROR_COLOR_BYTES / 16U;
    uint32_t different = 0;
    __asm__ volatile("1: ldmia %1!, {r4-r7}\n\tldmia %2!, {r8-r11}\n\t"
                     "eor r4,r4,r8\n\teor r5,r5,r9\n\teor r6,r6,r10\n\teor r7,r7,r11\n\t"
                     "orr r4,r4,r5\n\torr r6,r6,r7\n\torr r4,r4,r6\n\torr %0,%0,r4\n\t"
                     "subs %3,%3,#1\n\tbne 1b"
                     : "+&r"(different), "+&r"(actual), "+&r"(saved), "+&r"(chunks)
                     :
                     : "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "cc", "memory");
    return different == 0;
}
static __attribute__((noreturn)) void restoration_failed(void) {
    /* Do not return to an OS whose SRAM could not be restored. The validated
     * CX II display aperture can show a red screen without native calls. */
    uint32_t table;
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(table));
    table &= 0xffffc000U;
    if (table == 0xa4004000U && (REG(table + 0xa80U * 4U) & 0xfff00007U) == 0xa8000002U)
        for (unsigned i = 0; i < 320U * 240U; ++i)
            ((volatile uint16_t *)0xa8000000U)[i] = 0xf800U;
    drain();
    for (;;)
        __asm__ volatile("nop");
}
bool mirror_tables_end(void) {
    bool ok = true;
    if (state.borrowed) {
        uint32_t start = tick();
        copy((volatile uint32_t *)MIRROR_TABLE_SRAM, backup);
        state.restore += elapsed(start);
        start = tick();
        if (!restored()) {
            ++state.restore_errors;
            state.enabled = false;
            ok = false;
            copy((volatile uint32_t *)MIRROR_TABLE_SRAM, backup);
            if (!restored())
                restoration_failed();
        }
        state.verify += elapsed(start);
    }
    ++state.frames[state.borrowed];
    state.borrowed = false;
    native_critical_leave(state.entry_mask);
    return ok;
}
void mirror_tables_words(uint32_t words[32]) {
    memset(words, 0, 32U * sizeof(*words));
    words[0] = 1;
    words[1] = state.enabled;
    words[2] = state.phase;
    words[3] = state.frames[1];
    words[4] = state.frames[0];
    words[5] = state.skips[TABLE_USB_BUSY] + state.skips[TABLE_DMA_BUSY];
    words[6] = state.skips[TABLE_LAYOUT];
    words[7] = state.skips[TABLE_STACK];
    words[8] = state.restore_errors;
    words[9] = state.hz;
    const uint64_t totals[] = {state.convert[1], state.convert[0], state.setup, state.restore,
                               state.verify};
    for (unsigned i = 0; i < 5U; ++i) {
        words[10 + i * 2] = (uint32_t)totals[i];
        words[11 + i * 2] = (uint32_t)(totals[i] >> 32);
    }
    words[20] = state.reason;
    words[21] = state.timer_errors;
    words[22] = state.timer;
    words[23] = state.period;
    words[24] = state.usb_dma[0];
    words[25] = state.usb_dma[1];
    words[26] = state.host[0];
    words[27] = state.host[1];
    words[28] = state.dma_busy;
    words[29] = state.skips[TABLE_RAM_OFF];
    words[30] = MIRROR_COLOR_BYTES;
}
void mirror_tables_debug(FILE *file) {
    uint32_t words[32];
    mirror_tables_words(words);
    fprintf(file,
            "sram_tables build=1 enabled=%u phase=%u sram_frames=%lu ram_frames=%lu busy_skips=%lu "
            "layout_skips=%lu stack_skips=%lu restore_errors=%u timer_hz=%lu timer_errors=%u\n",
            state.enabled, state.phase, (unsigned long)state.frames[1],
            (unsigned long)state.frames[0], (unsigned long)words[5], (unsigned long)words[6],
            (unsigned long)words[7], state.restore_errors, (unsigned long)state.hz,
            state.timer_errors);
    fprintf(
        file,
        "sram_timings sram_convert_ticks=%llu ram_convert_ticks=%llu setup_ticks=%llu "
        "restore_ticks=%llu verify_ticks=%llu address=%08lx bytes=%u native_mapping_unchanged=1\n",
        (unsigned long long)state.convert[1], (unsigned long long)state.convert[0],
        (unsigned long long)state.setup, (unsigned long long)state.restore,
        (unsigned long long)state.verify, (unsigned long)MIRROR_TABLE_SRAM, MIRROR_COLOR_BYTES);
}
#endif
