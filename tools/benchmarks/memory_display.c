/* Compare real RGB565 output paths without reallocating OS SRAM or using DMA. */
#include <os.h>
#include <SDL/SDL.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "native_usb_ui.h"
#include "native_interrupts.h"
#include "nspire_hardware.h"
#include "rgb565.h"

#define FRAME_BYTES (320U * 240U * 2U)
#define REPEATS 64U
static const volatile uint32_t *counter;
static uint32_t hz, start_tick;
static volatile uint32_t sink;
static struct {
    int32_t table[5][256];
    uint8_t clip[384];
} colors __attribute__((aligned(32)));
static SDL_Surface *screen;
static nSDL_Font *font;
static char result_line[64] = "Enter / 5: run    Esc: exit";
static void edge_timing(void) {
    if (nspire_is_cx2() && *(volatile uint32_t *)0xc0000004U == 0x03780d3fU)
        *(volatile uint32_t *)0xc0000004U = 0x0720013fU;
}
void __wrap_SDL_StartTicks(void) {}
Uint32 __wrap_SDL_GetTicks(void) {
    return counter && hz ? (uint32_t)((uint64_t)(*counter - start_tick) * 1000U / hz) : 0;
}
bool sram_uses_native_clone(void) { return false; }
uint32_t sram_expected_ttbr(void) { return 0; }

static void draw(const char *message) {
    edge_timing();
    SDL_FillRect(screen, NULL, 0x1082);
    nSDL_DrawString(screen, font, 16, 20, "MEMORY / DISPLAY BENCHMARK");
    SDL_Rect rule = {16, 43, 288, 1};
    SDL_FillRect(screen, &rule, 0xfb60);
    nSDL_DrawString(screen, font, 16, 64, "Buffered vs direct VRAM conversion");
    nSDL_DrawString(screen, font, 16, 88, "Read-only SRAM timing comparison");
    nSDL_DrawString(screen, font, 16, 128, "%s", message);
    nSDL_DrawString(screen, font, 16, 184, "%s", result_line);
    nSDL_DrawString(screen, font, 16, 210, "No SRAM remapping. Clock unchanged.");
    lcd_blit(screen->pixels, SCR_320x240_565);
}
static void convert(const uint8_t *yuv, uint16_t *pixels) {
    for (unsigned y = 0; y < 240U; y += 2U)
        yuv420_rgb565_pair_rows(yuv + y * 320U, yuv + (y + 1U) * 320U, yuv + 76800U + y / 2U * 160U,
                                yuv + 96000U + y / 2U * 160U, pixels + y * 320U,
                                pixels + (y + 1U) * 320U, colors.table[0], 160U);
}
static void burst(void *destination, const void *source) {
    unsigned chunks = FRAME_BYTES / 32U;
    __asm__ volatile("1: ldmia %1!, {r4-r11}\n\tstmia %0!, {r4-r11}\n\t"
                     "subs %2, %2, #1\n\tbne 1b"
                     : "+r"(destination), "+r"(source), "+r"(chunks)
                     :
                     : "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "cc", "memory");
}
static uint32_t reads(const volatile uint32_t *data, unsigned bytes, unsigned *repeats) {
    native_critical_leave(0); /* The OS software tick requires native timer IRQs. */
    uint32_t start = *counter, sum = 0;
    uint32_t rtc_start = *(volatile uint32_t *)0x90090000U;
    *repeats = 0;
    do {
        for (unsigned repeat = 0; repeat < 16U; ++repeat)
            for (unsigned index = 0; index < bytes / 4U; ++index)
                sum ^= data[index];
        *repeats += 16U;
    } while (*counter - start < (hz + 3U) / 4U &&
             (uint64_t)*repeats * bytes < 256U * 1024U * 1024U &&
             *(volatile uint32_t *)0x90090000U - rtc_start < 2U);
    uint32_t elapsed = *counter - start;
    native_critical_enter();
    sink = sum;
    return elapsed;
}
static void run(FILE *log, uint8_t *yuv, uint8_t *memory) {
    uint32_t framebuffer = (uint32_t)(uintptr_t)REAL_SCREEN_BASE_ADDRESS;
    uint32_t ttbr;
    __asm__ volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(ttbr));
    ttbr &= 0xffffc000U;
    fprintf(log, "asic=%08lx framebuffer=%08lx lcd_type=%u tick_hz=%lu ttbr=%08lx\n",
            (unsigned long)nspire_hardware_id(), (unsigned long)framebuffer, lcd_type(),
            (unsigned long)hz, (unsigned long)ttbr);
    if (nspire_is_cx2())
        fprintf(log, "clock_unchanged=1 pll=%08lx divider=%08lx source=%08lx\n",
                (unsigned long)*(volatile uint32_t *)0x90140030U,
                (unsigned long)*(volatile uint32_t *)0x90140020U,
                (unsigned long)*(volatile uint32_t *)0x90140810U);
    /* Read only the native identity SRAM mapping. No alias or cache changes. */
    bool native_sram = nspire_is_cx2() && ttbr == 0xa4004000U &&
                       (((volatile uint32_t *)(uintptr_t)ttbr)[0xa40] & 0xfff00003U) == 0xa4000002U;
    uint32_t vram_entry = ttbr == 0xa4004000U ? ((volatile uint32_t *)(uintptr_t)ttbr)[0xa80] : 0;
    bool direct = nspire_is_cx2() && lcd_type() == SCR_320x240_565 && framebuffer == 0xa8000000U &&
                  (vram_entry & 0xfff00003U) == 0xa8000002U && !(vram_entry & 4U);
    fprintf(log, "vram_mapping=%08lx requires_identity_uncached_section=1\n",
            (unsigned long)vram_entry);
    fprintf(log, "native_sram_readable=%u direct_vram_layout=%u SRAM_WRITES=NONE VRAM_READS=NONE\n",
            native_sram, direct);
    for (unsigned round = 0; round < 4U; ++round) {
        for (unsigned size = 8192U; size <= 131072U; size *= 16U) {
            unsigned repeats;
            uint32_t dram = reads((volatile uint32_t *)memory, size, &repeats);
            fprintf(log, "read round=%u bytes=%u dram_repeats=%u dram_ticks=%lu dram_ns_word=%llu",
                    round, size, repeats, (unsigned long)dram,
                    (unsigned long long)((uint64_t)dram * 1000000000U / hz /
                                         ((uint64_t)repeats * (size / 4U))));
            if (native_sram) {
                uint32_t sram = reads((volatile uint32_t *)(uintptr_t)0xa4020000U, size, &repeats);
                fprintf(log,
                        " native_sram_repeats=%u native_sram_ticks=%lu native_sram_ns_word=%llu",
                        repeats, (unsigned long)sram,
                        (unsigned long long)((uint64_t)sram * 1000000000U / hz /
                                             ((uint64_t)repeats * (size / 4U))));
            } else
                fprintf(log, " native_sram=UNSUPPORTED_MAPPING");
            fputc('\n', log);
            if (!dram)
                fprintf(log, "read_clock_error=NO_TIMER_PROGRESS\n");
            fflush(log);
        }
        for (unsigned index = 0; index < 3U; ++index) {
            unsigned mode = (index + round) % 3U; /* Alternate order between rounds. */
            if (mode && !direct) {
                fprintf(log, "render round=%u mode=%u SKIPPED_NONLANDSCAPE_VRAM\n", round, mode);
                continue;
            }
            draw(mode == 0   ? "Testing buffered native blit..."
                 : mode == 1 ? "Testing buffered burst copy..."
                             : "Testing direct VRAM conversion...");
            (void)native_usb_ui_wait();
            native_critical_enter();
            native_critical_leave(0);
            uint32_t start = *counter;
            for (unsigned frame = 0; frame < REPEATS; ++frame) {
                if (mode == 2)
                    convert(yuv, (uint16_t *)(uintptr_t)framebuffer);
                else {
                    convert(yuv, screen->pixels);
                    if (mode == 0)
                        lcd_blit(screen->pixels, SCR_320x240_565);
                    else
                        burst((void *)(uintptr_t)framebuffer, screen->pixels);
                }
            }
            uint32_t elapsed = *counter - start;
            native_critical_enter();
            fprintf(log, "render round=%u mode=%u frames=%u ticks=%lu total_us=%llu\n", round, mode,
                    REPEATS, (unsigned long)elapsed,
                    (unsigned long long)((uint64_t)elapsed * 1000000U / hz));
            fflush(log);
            /* Hold the final pattern briefly for visual comparison. */
            for (unsigned wait = 0; wait < 20U; ++wait) {
                (void)native_usb_ui_wait();
                native_critical_enter();
            }
        }
    }
    fprintf(log, "RESULT completed; native interrupts active, no USB streaming; visual "
                 "verification required\n");
}
int main(int argc, char **argv) {
    int status = 1, policy = 0;
    bool policy_owned = false, display_owned = false;
    uint8_t *yuv = NULL, *memory = NULL;
    FILE *log = NULL;
    nspire_hardware_init();
    if (!native_usb_ui_clock(&counter, &hz, NULL, NULL))
        goto done;
    start_tick = *counter;
    policy = TCT_Local_Control_Interrupts(0);
    policy_owned = true;
    native_critical_enter();
    if (native_usb_ui_acquire(NULL, NULL))
        goto done;
    display_owned = true;
    if (SDL_Init(SDL_INIT_VIDEO) || !(screen = SDL_SetVideoMode(320, 240, 16, SDL_SWSURFACE)))
        goto done;
    font = nSDL_LoadFont(NSDL_FONT_TINYTYPE, 235, 235, 235);
    yuv = malloc(115200U);
    memory = malloc(131072U);
    if (!font || !yuv || !memory || ((uintptr_t)screen->pixels & 3U))
        goto done;
    memset(memory, 0x5a, 131072U);
    for (unsigned i = 0; i < 76800U; ++i) {
        unsigned x = i % 320U, y = i / 320U;
        yuv[i] = x == 0 || x == 319 || y == 0 || y == 239 ? 235 : (uint8_t)(16U + (y + x) % 220U);
    }
    for (unsigned i = 76800U; i < 115200U; ++i)
        yuv[i] = (uint8_t)(80U + i % 96U);
    for (int i = 0; i < 256; ++i) {
        int y = i - 16, c = i - 128;
        if (y < 0)
            y = 0;
        colors.table[0][i] = 298 * y + 128;
        colors.table[1][i] = 516 * c + 131072;
        colors.table[2][i] = -100 * c;
        colors.table[3][i] = 409 * c + 131072;
        colors.table[4][i] = -208 * c + 262144;
    }
    for (int i = 0; i < 384; ++i) {
        int limit = i < 192 ? 31 : 63, value = i - (i < 192 ? 64 : 256);
        colors.clip[i] = value < 0 ? 0 : value > limit ? limit : value;
    }
    char path[1024];
    const char *program = argc ? argv[0] : NULL;
    const char *slash = program ? strrchr(program, '/') : NULL;
    if (slash && slash - program < 900)
        snprintf(path, sizeof(path), "%.*s/ndmemory.log.tns", (int)(slash - program), program);
    else
        strcpy(path, "ndmemory.log.tns");
    draw("Ready. Tests repeat four times.");
    bool previous = false;
    for (;;) {
        if (isKeyPressed(KEY_NSPIRE_ESC))
            break;
        bool start = isKeyPressed(KEY_NSPIRE_ENTER) || isKeyPressed(KEY_NSPIRE_5) ||
                     isKeyPressed(KEY_NSPIRE_CLICK);
        if (start && !previous) {
            log = fopen(path, "wb");
            if (log) {
                fprintf(log, "ND memory/display benchmark | %s %s\n", __DATE__, __TIME__);
                run(log, yuv, memory);
                fclose(log);
                log = NULL;
                strcpy(result_line, "Log: ndmemory.log.tns   Esc: exit");
                draw("Complete. Compare all three patterns.");
            } else
                draw("Could not create ndmemory.log.tns");
        }
        previous = start;
        (void)native_usb_ui_wait();
        native_critical_enter();
    }
    status = 0;
done:
    if (log)
        fclose(log);
    free(memory);
    free(yuv);
    if (font)
        nSDL_FreeFont(font);
    SDL_Quit();
    native_critical_enter();
    if (display_owned) {
        while (native_usb_ui_release()) {
            (void)native_usb_ui_wait();
            native_critical_enter();
        }
    }
    if (policy_owned)
        TCT_Local_Control_Interrupts(policy);
    return status;
}
