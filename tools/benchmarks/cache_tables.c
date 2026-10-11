/* Standalone table-placement experiment. No receiver or USB service is started.
 * SRAM is borrowed only within an IRQ/FIQ-masked interval and restored before
 * any native call. No MMU, timer, clock or DMA register is changed. */
#include <os.h>
#include <SDL/SDL.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cache_plan.h"
#include "native_interrupts.h"
#include "native_usb_ui.h"
#include "nspire_hardware.h"
#include "profile.h"
#include "rgb565.h"

#define REG(a) (*(const volatile uint32_t *)(uintptr_t)(a))
#define FRAME_BYTES 153600U
#define REPEATS 16U
enum { NORMAL, WARM, PIN_HOT, PIN_ALL, SRAM, MODES };
typedef struct {
    int32_t table[5][256];
    uint8_t clip[384];
} ColorTables;
typedef struct {
    uint64_t setup, pressure, convert, restore;
    unsigned frames;
} Timing;
static const char *names[] = {"RAM normal", "RAM warmed", "RAM pin hot", "RAM pin all",
                              "SRAM tables"};
static const volatile uint32_t *counter;
static uint32_t hz, first_tick, timer_base, timer_period, timer_hz;
static unsigned timer_errors;
static SDL_Surface *screen;
static nSDL_Font *font;
static volatile uint32_t sink;
static ColorTables *colors;
static uint32_t backup[COLOR_BYTES / 4U];
static bool can_pin, can_sram, can_direct;
static char result_line[72] = "Enter / 5: run     Esc: exit";

void __wrap_SDL_StartTicks(void) {}
Uint32 __wrap_SDL_GetTicks(void) {
    return counter && hz ? (uint32_t)((uint64_t)(*counter - first_tick) * 1000U / hz) : 0;
}
bool sram_uses_native_clone(void) { return false; }
uint32_t sram_expected_ttbr(void) { return 0; }
static uint32_t ttbr(void) {
    uint32_t value;
    __asm__ volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(value));
    return value & 0xffffc000U;
}
static uint32_t lock_bits(void) {
    uint32_t value;
    __asm__ volatile("mrc p15, 0, %0, c9, c0, 0" : "=r"(value));
    return value;
}
static void unlock(uint32_t saved) {
    uint32_t current = lock_bits();
    current = (current & ~15U) | (saved & 15U);
    __asm__ volatile("mcr p15, 0, %0, c9, c0, 0" ::"r"(current) : "memory");
}
/* All data accesses during selective linefill are table loads. In particular,
 * no C stack spill or literal-pool data load can evict the line just loaded.
 * A 2048-byte-aligned block has one line per set in one 2 KiB cache way.
 * Hot mode loads Y[0..1023] and clip[5120..5503] into disjoint sets of way 0. */
static __attribute__((naked, noinline)) uint32_t pin_tables(const void *base
                                                            __attribute__((unused)),
                                                            unsigned mode __attribute__((unused))) {
    __asm__ volatile("push {r4-r10,lr}\n"
                     "mov r4,r0\nmov r10,r1\nmrc p15,0,r7,c9,c0,0\n"
                     "add r1,r4,#5120\nadd r1,r1,#384\nmov r0,r4\n"
                     "1: mcr p15,0,r0,c7,c14,1\nadd r0,r0,#32\ncmp r0,r1\nblo 1b\n"
                     "mov r0,#0\nmcr p15,0,r0,c7,c10,4\n"
                     "cmp r10,#2\nbeq 4f\n"
                     "bic r0,r7,#15\norr r0,r0,#14\nmcr p15,0,r0,c9,c0,0\n"
                     "mov r0,r4\nadd r1,r4,#1024\n"
                     "2: ldr r12,[r0],#32\ncmp r0,r1\nblo 2b\n"
                     "add r0,r4,#5120\nadd r1,r0,#384\n"
                     "3: ldr r12,[r0],#32\ncmp r0,r1\nblo 3b\n"
                     "mov r9,#1\nb 8f\n"
                     "4: mov r8,#1\nmov r9,#0\nmov r0,r4\n"
                     "5: bic r2,r7,#15\nmov r3,#15\neor r3,r3,r8\norr r2,r2,r3\n"
                     "mcr p15,0,r2,c9,c0,0\nadd r1,r0,#2048\n"
                     "add r2,r4,#5120\nadd r2,r2,#384\ncmp r1,r2\nmovhi r1,r2\n"
                     "6: ldr r12,[r0],#32\ncmp r0,r1\nblo 6b\n"
                     "orr r9,r9,r8\ncmp r0,r2\nbhs 8f\nmov r8,r8,lsl #1\nb 5b\n"
                     "8: bic r0,r7,#15\norr r0,r0,r9\nmcr p15,0,r0,c9,c0,0\n"
                     "mov r0,r7\npop {r4-r10,pc}\n");
}
static __attribute__((naked, noinline)) uint32_t banked_sp(unsigned mode __attribute__((unused))) {
    __asm__ volatile("mrs r1,cpsr\nbic r2,r1,#31\norr r2,r2,r0\nmsr cpsr_c,r2\n"
                     "mov r0,sp\nmsr cpsr_c,r1\nbx lr\n");
}
static uint32_t tick(void) { return REG(timer_base + 4U); }
static uint32_t delta(uint32_t before) {
    uint32_t after = tick();
    if (before >= timer_period || after >= timer_period) {
        ++timer_errors;
        return 0;
    }
    return before >= after ? before - after : before + timer_period - after;
}
static void drain(void) {
    uint32_t zero = 0;
    __asm__ volatile("mcr p15,0,%0,c7,c10,4" ::"r"(zero) : "memory");
}
static bool usb_idle(void) {
    /* FOTG210: both host run bits and device DMA-start bits must be clear.
     * This is a deliberately narrower condition than 'nothing connected'. */
    return !(REG(0xb0000010U) & 1U) && !(REG(0xb4000010U) & 1U) && !(REG(0xb00001c8U) & 1U) &&
           !(REG(0xb40001c8U) & 1U);
}
static bool dma_idle(void) {
    if (REG(0xbc000030U) != 0x00011900U || REG(0xbc000034U) != 0x00006303U)
        return false;
    for (unsigned ch = 0; ch < 6U; ++ch)
        if ((REG(0xbc000100U + ch * 32U) & 1U) || (REG(0xbc000104U + ch * 32U) & 0x100U))
            return false;
    return true;
}
static bool sram_ready(void) {
    uint32_t table = ttbr();
    if (table != 0xa4004000U ||
        !sram_plan_supported(nspire_hardware_id(), table, REG(table + 0xa40U * 4U), usb_idle(),
                             dma_idle()))
        return false;
    static const unsigned modes[] = {0x1fU, 0x12U, 0x11U, 0x17U, 0x1bU, 0x13U};
    for (unsigned i = 0; i < sizeof(modes) / sizeof(modes[0]); ++i)
        if (!sram_stack_clear(banked_sp(modes[i])))
            return false;
    return true;
}
static void copy_words(volatile uint32_t *destination, const volatile uint32_t *source) {
    for (unsigned i = 0; i < COLOR_BYTES / 4U; ++i)
        destination[i] = source[i];
    drain();
}
static void warm_tables(void) {
    const volatile uint32_t *words = (const volatile uint32_t *)colors;
    uint32_t sum = 0;
    for (unsigned i = 0; i < COLOR_BYTES / 4U; i += 8U)
        sum ^= words[i];
    sink = sum;
}
static void edge_timing(void) {
    if (nspire_is_cx2() && REG(0xc0000004U) == 0x03780d3fU)
        *(volatile uint32_t *)0xc0000004U = 0x0720013fU;
}
static void draw(const char *message) {
    edge_timing();
    SDL_FillRect(screen, NULL, 0x1082);
    nSDL_DrawString(screen, font, 12, 16, "COLOUR TABLE BENCHMARK");
    SDL_Rect rule = {12, 38, 296, 1};
    SDL_FillRect(screen, &rule, 0xfb60);
    nSDL_DrawString(screen, font, 12, 56, "RAM / cache lockdown / SRAM");
    nSDL_DrawString(screen, font, 12, 80, "Unplug USB before starting.");
    nSDL_DrawString(screen, font, 12, 114, "%s", message);
    nSDL_DrawString(screen, font, 12, 162, "Four rounds; output checked.");
    nSDL_DrawString(screen, font, 12, 190, "%s", result_line);
    nSDL_DrawString(screen, font, 12, 214, "Clock unchanged. Separate test app.");
    lcd_blit(screen->pixels, SCR_320x240_565);
}
static void convert_rows(const uint8_t *yuv, uint16_t *pixels, const ColorTables *tables,
                         unsigned first, unsigned last) {
    for (unsigned y = first; y < last; y += 2U)
        yuv420_rgb565_pair_rows(yuv + y * 320U, yuv + (y + 1U) * 320U, yuv + 76800U + y / 2U * 160U,
                                yuv + 96000U + y / 2U * 160U, pixels + y * 320U,
                                pixels + (y + 1U) * 320U, tables->table[0], 160U);
}
static uint64_t conversion(const uint8_t *yuv, uint16_t *pixels, const ColorTables *tables) {
    uint64_t total = 0;
    /* Each eight-row sample is far shorter than the native 10 ms period.
     * Taking modulo timer deltas per strip avoids relying on masked OS ticks. */
    for (unsigned row = 0; row < 240U; row += 8U) {
        uint32_t start = tick();
        convert_rows(yuv, pixels, tables, row, row + 8U);
        drain();
        total += delta(start);
    }
    return total;
}
static uint64_t pressure(const uint32_t *memory) {
    uint32_t sum = 0;
    uint64_t total = 0;
    for (unsigned block = 0; block < 16U; ++block) {
        uint32_t start = tick();
        const volatile uint32_t *data = memory + block * 1024U;
        for (unsigned i = 0; i < 1024U; ++i)
            sum ^= data[i];
        total += delta(start);
    }
    sink = sum;
    return total;
}
static bool frame(unsigned mode, const uint8_t *yuv, uint16_t *pixels, const uint32_t *memory,
                  bool with_pressure, Timing *out) {
    unsigned mask = native_critical_enter();
    uint32_t old_lock = lock_bits(), start = tick();
    ColorTables *tables = colors;
    bool borrowed = false;
    if (mode == SRAM) {
        if (!sram_ready()) {
            native_critical_leave(mask);
            return false;
        }
        tables = (ColorTables *)(uintptr_t)SRAM_TABLE_ADDRESS;
        copy_words(backup, (volatile uint32_t *)tables);
        copy_words((volatile uint32_t *)tables, (volatile uint32_t *)colors);
        borrowed = true;
    } else if (mode == PIN_HOT || mode == PIN_ALL) {
        if (!can_pin || (old_lock & 15U)) {
            native_critical_leave(mask);
            return false;
        }
        old_lock = pin_tables(colors, mode == PIN_ALL ? 2U : 1U);
        if ((lock_bits() & 15U) != (mode == PIN_ALL ? 7U : 1U)) {
            unlock(old_lock);
            native_critical_leave(mask);
            return false;
        }
    } else if (mode == WARM)
        warm_tables();
    out->setup += delta(start);
    if (with_pressure)
        out->pressure += pressure(memory);
    out->convert += conversion(yuv, pixels, tables);
    start = tick();
    if (mode == PIN_HOT || mode == PIN_ALL)
        unlock(old_lock);
    if (borrowed)
        copy_words((volatile uint32_t *)tables, backup);
    out->restore += delta(start);
    ++out->frames;
    /* No OS/stdio/SDL calls occurred with SRAM overwritten or ways reserved. */
    native_critical_leave(mask);
    return true;
}
static uint32_t hash(const void *data, unsigned bytes) {
    const uint8_t *p = data;
    uint32_t result = 2166136261U;
    for (unsigned i = 0; i < bytes; ++i)
        result = (result ^ p[i]) * 16777619U;
    return result;
}
static bool mapping_cached(uintptr_t address) {
    uint32_t table = ttbr(), descriptor = REG(table + (address >> 20) * 4U);
    if ((descriptor & 3U) == 2U)
        return (descriptor & 4U) != 0;
    if ((descriptor & 3U) != 1U)
        return false;
    uint32_t small = REG((descriptor & 0xfffffc00U) + ((address >> 12) & 255U) * 4U);
    return (small & 3U) && (small & 4U);
}
static void run(FILE *log, const uint8_t *yuv, uint16_t *reference, const uint32_t *memory) {
    uint32_t type, control;
    __asm__ volatile("mrc p15,0,%0,c0,c0,1" : "=r"(type));
    __asm__ volatile("mrc p15,0,%0,c1,c0,0" : "=r"(control));
    unsigned mask = native_critical_enter();
    can_pin = cache_plan_supported(type, control, lock_bits()) &&
              mapping_cached((uintptr_t)colors) &&
              mapping_cached((uintptr_t)colors + COLOR_BYTES - 1U);
    can_sram = sram_ready();
    uint32_t table = ttbr(), vram = table == 0xa4004000U ? REG(table + 0xa80U * 4U) : 0;
    can_direct = nspire_is_cx2() && lcd_type() == SCR_320x240_565 &&
                 (uintptr_t)REAL_SCREEN_BASE_ADDRESS == 0xa8000000U &&
                 (vram & 0xfff00007U) == 0xa8000002U;
    native_critical_leave(mask);
    fprintf(log, "asic=%08lx ttbr=%08lx control=%08lx cache_type=%08lx lockdown=%08lx\n",
            (unsigned long)nspire_hardware_id(), (unsigned long)table, (unsigned long)control,
            (unsigned long)type, (unsigned long)lock_bits());
    fprintf(log,
            "tables=%08lx bytes=%u aligned_2048=%u pin_supported=%u sram_supported=%u "
            "direct_supported=%u\n",
            (unsigned long)(uintptr_t)colors, COLOR_BYTES, !((uintptr_t)colors & 2047U), can_pin,
            can_sram, can_direct);
    fprintf(
        log,
        "usb_top_command=%08lx usb_bottom_command=%08lx usb_top_dma=%08lx usb_bottom_dma=%08lx\n",
        (unsigned long)REG(0xb0000010U), (unsigned long)REG(0xb4000010U),
        (unsigned long)REG(0xb00001c8U), (unsigned long)REG(0xb40001c8U));
    fprintf(log,
            "timer_base=%08lx period=%lu timer_hz=%lu strip_rows=8 unchanged_clock=1 pll=%08lx\n",
            (unsigned long)timer_base, (unsigned long)timer_period, (unsigned long)timer_hz,
            (unsigned long)REG(0x90140030U));
    fflush(log);
    convert_rows(yuv, reference, colors, 0, 240);
    uint32_t expected = hash(reference, FRAME_BYTES);
    bool good = true;
    for (unsigned round = 0; round < 4U && good; ++round)
        for (unsigned index = 0; index < MODES && good; ++index) {
            unsigned mode = (index + round) % MODES;
            if (((mode == PIN_HOT || mode == PIN_ALL) && !can_pin) || (mode == SRAM && !can_sram)) {
                fprintf(log, "round=%u mode=%s SKIPPED_%s\n", round, names[mode],
                        mode == SRAM ? "SRAM_MAPPING_STACK_OR_DMA_BUSY"
                                     : "CACHE_GEOMETRY_MAPPING_OR_OS_LOCKED");
                fflush(log);
                continue;
            }
            draw(names[mode]);
            (void)native_usb_ui_wait();
            native_critical_enter();
            Timing correctness = {0};
            if (!frame(mode, yuv, screen->pixels, memory, true, &correctness)) {
                good = false;
                break;
            }
            uint32_t actual = hash(screen->pixels, FRAME_BYTES);
            fprintf(log, "verify round=%u mode=%s expected=%08lx actual=%08lx match=%u\n", round,
                    names[mode], (unsigned long)expected, (unsigned long)actual,
                    actual == expected);
            if (actual != expected) {
                good = false;
                break;
            }
            for (unsigned direct = 0; direct < 2U && good; ++direct) {
                if (direct && !can_direct)
                    continue;
                for (unsigned loaded = 0; loaded < 2U && good; ++loaded) {
                    Timing elapsed = {0};
                    uint16_t *target = direct ? (uint16_t *)0xa8000000U : screen->pixels;
                    for (unsigned repeat = 0; repeat < REPEATS; ++repeat) {
                        if (isKeyPressed(KEY_NSPIRE_ESC)) {
                            good = false;
                            break;
                        }
                        if (!frame(mode, yuv, target, memory, loaded, &elapsed)) {
                            good = false;
                            break;
                        }
                        if (!direct && hash(target, FRAME_BYTES) != expected) {
                            fprintf(log, "ERROR round=%u mode=%s repeat=%u RAM_OUTPUT_MISMATCH\n",
                                    round, names[mode], repeat);
                            good = false;
                            break;
                        }
                        (void)native_usb_ui_wait();
                        native_critical_enter();
                    }
                    fprintf(log,
                            "timing round=%u mode=%s target=%s pressure=%u frames=%u setup_us=%llu "
                            "pressure_us=%llu convert_us=%llu restore_us=%llu\n",
                            round, names[mode], direct ? "VRAM" : "RAM", loaded, elapsed.frames,
                            (unsigned long long)(elapsed.setup * 1000000U / timer_hz),
                            (unsigned long long)(elapsed.pressure * 1000000U / timer_hz),
                            (unsigned long long)(elapsed.convert * 1000000U / timer_hz),
                            (unsigned long long)(elapsed.restore * 1000000U / timer_hz));
                    fflush(log);
                }
            }
        }
    fprintf(log,
            "RESULT completed=%u timer_errors=%u restored_lockdown=%08lx native_ttbr=%08lx "
            "SRAM_NOT_RESERVED_FOR_USB=1\n",
            good, timer_errors, (unsigned long)lock_bits(), (unsigned long)ttbr());
    snprintf(result_line, sizeof(result_line), "%s%s. ndcache.log.tns",
             good ? "Complete" : "Stopped",
             !can_sram  ? " (SRAM skipped)"
             : !can_pin ? " (pinning skipped)"
                        : "");
}
int main(int argc, char **argv) {
    int status = 1, policy = 0;
    bool policy_owned = false, display_owned = false;
    uint8_t *yuv = NULL;
    uint16_t *reference = NULL;
    uint32_t *memory = NULL;
    void *allocation = NULL;
    nspire_hardware_init();
    if (!nspire_is_cx2() || !native_usb_ui_clock(&counter, &hz, NULL, NULL))
        goto done;
    first_tick = *counter;
    policy = TCT_Local_Control_Interrupts(0);
    policy_owned = true;
    native_critical_enter();
    if (native_usb_ui_acquire(NULL, NULL))
        goto done;
    display_owned = true;
    if (SDL_Init(SDL_INIT_VIDEO) || !(screen = SDL_SetVideoMode(320, 240, 16, SDL_SWSURFACE)))
        goto done;
    font = nSDL_LoadFont(NSDL_FONT_TINYTYPE, 235, 235, 235);
    allocation = malloc(COLOR_BYTES + 2047U);
    yuv = malloc(115200U);
    reference = malloc(FRAME_BYTES);
    memory = malloc(65536U);
    if (!font || !allocation || !yuv || !reference || !memory)
        goto done;
    colors = (ColorTables *)(((uintptr_t)allocation + 2047U) & ~(uintptr_t)2047U);
    for (int i = 0; i < 256; ++i) {
        int y = i - 16, c = i - 128;
        if (y < 0)
            y = 0;
        colors->table[0][i] = 298 * y + 128;
        colors->table[1][i] = 516 * c + 131072;
        colors->table[2][i] = -100 * c;
        colors->table[3][i] = 409 * c + 131072;
        colors->table[4][i] = -208 * c + 262144;
    }
    for (int i = 0; i < 384; ++i) {
        int limit = i < 192 ? 31 : 63, v = i - (i < 192 ? 64 : 256);
        colors->clip[i] = v < 0 ? 0 : v > limit ? limit : v;
    }
    for (unsigned i = 0; i < 115200U; ++i)
        yuv[i] = (uint8_t)((i * 73U + i / 320U * 19U) & 255U);
    for (unsigned i = 0; i < 16384U; ++i)
        memory[i] = i * 2654435761U;
    profile_start(counter, hz);
    if (!profile.high_resolution) {
        draw("No suitable native timing source.");
        goto wait_exit;
    }
    timer_base = profile.timer_base;
    timer_period = profile.period;
    timer_hz = profile.hz;
    char path[1024];
    const char *program = argc ? argv[0] : NULL;
    const char *slash = program ? strrchr(program, '/') : NULL;
    if (slash && slash - program < 900)
        snprintf(path, sizeof(path), "%.*s/ndcache.log.tns", (int)(slash - program), program);
    else
        strcpy(path, "ndcache.log.tns");
    draw("Ready. Tests repeat four times.");
    bool previous = false;
    for (;;) {
        if (isKeyPressed(KEY_NSPIRE_ESC))
            break;
        bool start = isKeyPressed(KEY_NSPIRE_ENTER) || isKeyPressed(KEY_NSPIRE_5) ||
                     isKeyPressed(KEY_NSPIRE_CLICK);
        if (start && !previous) {
            FILE *log = fopen(path, "wb");
            if (log) {
                fprintf(log, "ND colour-table benchmark | %s %s\n", __DATE__, __TIME__);
                run(log, yuv, reference, memory);
                fclose(log);
                draw("Finished. Esc exits.");
            } else
                draw("Could not save ndcache.log.tns");
        }
        previous = start;
        (void)native_usb_ui_wait();
        native_critical_enter();
    }
    status = 0;
    goto done;
wait_exit:
    while (!isKeyPressed(KEY_NSPIRE_ESC)) {
        (void)native_usb_ui_wait();
        native_critical_enter();
    }
done:
    free(memory);
    free(reference);
    free(yuv);
    free(allocation);
    if (font)
        nSDL_FreeFont(font);
    SDL_Quit();
    native_critical_enter();
    if (display_owned)
        while (native_usb_ui_release()) {
            (void)native_usb_ui_wait();
            native_critical_enter();
        }
    if (policy_owned)
        TCT_Local_Control_Interrupts(policy);
    return status;
}
