#include <os.h>
#include <SDL/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include <time.h>
#include "diagnostic_log.h"
#include "wire.h"
#include "input.h"
#include "profile.h"
#include "decoder.h"
#include "decoder_guard.h"
#if MIRROR_CAPTURE_INPUT
#include "decoder_input.h"
#endif
#include "pixels.h"
#include "display_target.h"
#include "display_rate.h"
#include "mpeg4_xvid.h"
#include "performance_clock.h"
#include "native_usb_ui.h"
#include "native_interrupts.h"
#include "nspire_hardware.h"
#include "sram_tables.h"
#include "channel_read.h"
#include "worker_wait.h"
#include "nnse_abort.h"
#include "idle_timing.h"
#include "portable_reader_platform.h"
#ifndef MIRROR_FORCE_NATIVE_ABORT
#define MIRROR_FORCE_NATIVE_ABORT 0
#endif
static MirrorQueue queue;
static MirrorInput input_queue;
static volatile bool frame_active, decode_cpu;
static volatile bool closing;
static volatile unsigned callbacks;
static int transport_error;
static unsigned packet_limit;
static uintptr_t channel_read_export, channel_read_target, channel_write_export,
    current_task_export;
static volatile unsigned callback_io;
static volatile uint32_t callback_io_started, maximum_read_ms, maximum_write_ms;
static volatile uintptr_t callback_task;
static MirrorWorkerWait longest_worker_wait;
static uint32_t worker_wait_ms;
static unsigned worker_wait_io;
static uintptr_t nnse_abort_target;
static unsigned nnse_abort_attempts;
static int nnse_abort_result;
static uint32_t nnse_capability, nnse_otg, nnse_detached_since;
static uint32_t nnse_connector_hash, nnse_cleanup_hash, nnse_ws_mode;
static bool nnse_detached_seen;
static uint8_t service_input[MIRROR_PACKET];
static uint8_t service_reply[MIRROR_SRAM_TABLES ? 360U : INPUT_REPLY];
static unsigned close_waits;
static MirrorLogHistory log_history;
static int16_t close_result;
static const char *program_path;
static void write_log(const char *stage, int result);
static const volatile uint32_t *os_counter;
static uint32_t os_hz, clock_start;
static SDL_Surface *screen;
static nSDL_Font *font;
static char notice[120];
static bool save_log;
static bool log_enabled = MIRROR_DIAGNOSTIC_AUTOSAVE != 0;
static struct {
    uint32_t frame, bytes, offset, code, detail, bit, mhz;
    uint32_t resume_mb, resume_skip, resume_bit, resume_decoded, resume_pointer;
    int32_t resume_qp;
    uint32_t input_hash;
    char stage[64], reason[64];
} decode_failures[8];
static unsigned decode_failure_count;
#if MIRROR_DECODE_GUARD
static MirrorDecodeGuard decode_guard;
#endif
#if MIRROR_CAPTURE_INPUT
static MirrorDecoderInput decode_input;
static uint32_t decode_input_saved_frame, decode_input_saved_hash;
static uint32_t decode_input_saved_post_hash;
static bool decode_input_saved;
static uint32_t applied_pll, applied_emi74, applied_emi1c;
static uint32_t timing_checked, timing_mismatched, timing_first_frame, timing_first_emi1c;
#endif
static uint32_t direct_presented, buffered_presented;
static bool have_picture;
#ifndef MIRROR_DIRECT_VRAM
#define MIRROR_DIRECT_VRAM 0
#endif
static volatile MirrorDisplayRate display_rate;
static int clock_pending = -1;
#if MIRROR_IDLE_TIMING
static MirrorIdleTiming idle_timing;
static uint32_t timing_rom_hash, timing_sram_hash, timing_mapping, timing_pointer, timing_ttbr;
static bool timing_read(void *unused, uint32_t address, uint32_t *value) {
    (void)unused;
    if (address != 0x10c52e3cU &&
        !(address >= MIRROR_IDLE_ENTRY && address < MIRROR_IDLE_ENTRY + 344U && !(address & 3U)))
        return false;
    *value = *(volatile uint32_t *)(uintptr_t)address;
    return true;
}
static bool timing_write(void *unused, uint32_t address, uint32_t value) {
    (void)unused;
    if (!idle_timing.supported || address != MIRROR_IDLE_LITERAL ||
        (value != 0x502U && value != 0x528U))
        return false;
    uint32_t table;
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(table));
    table &= 0xffffc000U;
    if (table != 0xa4004000U ||
        (((volatile uint32_t *)(uintptr_t)table)[0xa40] & 0xfff00c07U) != 0xa4000c02U ||
        *(volatile uint32_t *)0x10c52e3cU != MIRROR_IDLE_ENTRY)
        return false;
    *(volatile uint32_t *)(uintptr_t)address = value;
    __asm__ volatile("mcr p15,0,%0,c7,c10,4" :: "r"(0) : "memory");
    return *(volatile uint32_t *)(uintptr_t)address == value;
}
static const MirrorTimingAccess timing_access = {NULL, timing_read, timing_write};
static bool timing_release(void) {
    return mirror_timing_release(&idle_timing, &timing_access);
}
#endif
static unsigned current_mhz(void) {
    unsigned known = performance_clock_current_mhz();
    if (known)
        return known;
    if (nspire_is_cx2()) {
        uint32_t pll = *(volatile uint32_t *)0x90140030U,
                 divider = *(volatile uint32_t *)0x90140020U;
        if (!(*(volatile uint32_t *)0x90140810U & 0x10U))
            return 48;
        if (pll & 0x10U)
            return 24;
        unsigned input = (pll >> 16) & 31U;
        return (pll & 1U) && input
                   ? (24U * ((pll >> 24) & 63U) / input) / (((divider >> 20) & 15U) + 1U)
                   : 0;
    }
    uint32_t value = *(volatile uint32_t *)0x900b0024U;
    if (value & 0x100U)
        return 48U >> (value >> 30);
    unsigned divisor = value & 0xfeU;
    if (((value >> 21) & 3U) == 1U)
        divisor >>= 1;
    return divisor ? 6U * ((value >> 15) & 63U) / divisor : 0;
}
static unsigned capabilities(void) {
    return MIRROR_CAP_BULK | MIRROR_CAP_PROBE | MIRROR_CAP_RAW | MIRROR_CAP_PIXELS |
           MIRROR_CAP_CLOCK | MIRROR_CAP_INPUT | MIRROR_CAP_MPEG4 | MIRROR_CAP_STATS |
           MIRROR_CAP_STREAM | MIRROR_CAP_PAUSE | MIRROR_CAP_DISPLAY_RATE |
           MIRROR_CAP_PAUSE_PEER_CLOSE |
           (nspire_is_cx2() ? MIRROR_CAP_CX2 : 0) |
           (MIRROR_SRAM_TABLES && nspire_is_cx2() ? MIRROR_CAP_SRAM_TABLES : 0);
}
/* SDL's timer initialization must not overwrite Nucleus's USB scheduler clock. */
void __wrap_SDL_StartTicks(void) {}
Uint32 __wrap_SDL_GetTicks(void) {
    return os_counter && os_hz ? (uint32_t)(((uint64_t)(*os_counter - clock_start) * 1000U) / os_hz)
                               : 0;
}
/* No SRAM remapping: native callbacks and scheduler retain their own mappings. */
bool sram_uses_native_clone(void) { return false; }
uint32_t sram_expected_ttbr(void) { return 0; }
static void edge_timing(void) {
    if (nspire_is_cx2() &&
        (*(volatile uint32_t *)0xc0000004U == 0x03780d3fU || lcd_type() == SCR_240x320_565))
        *(volatile uint32_t *)0xc0000004U = 0x0720013fU;
}
static void present(void) {
    edge_timing();
    lcd_blit(screen->pixels, SCR_320x240_565);
}
static uint16_t *frame_target(bool overlay, bool h264) {
    if (MIRROR_DIRECT_VRAM && nspire_is_cx2()) {
        uint32_t table;
        __asm__ volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(table));
        table &= 0xffffc000U;
        uint32_t mapping =
            table == 0xa4004000U ? ((volatile uint32_t *)(uintptr_t)table)[0xa80] : 0;
        uint32_t framebuffer = (uint32_t)(uintptr_t)REAL_SCREEN_BASE_ADDRESS;
        if (mirror_direct_vram_eligible(true, lcd_type() == SCR_320x240_565, framebuffer, mapping,
                                        screen->pitch, overlay, h264)) {
            edge_timing();
            return (uint16_t *)(uintptr_t)framebuffer;
        }
    }
    return screen->pixels;
}
static void waiting(const char *status) {
    SDL_FillRect(screen, NULL, 0x1082);
    SDL_Rect rule = {18, 43, 284, 1};
    SDL_FillRect(screen, &rule, 0xfb60);
    nSDL_DrawString(screen, font, 18, 20, "ND SCREEN MIRROR");
    nSDL_DrawString(screen, font, 18, 82, "%s", status);
    nSDL_DrawString(screen, font, 18, 109, "Connect to the PC with a USB cable.");
    nSDL_DrawString(screen, font, 18, 130, "Start ND Screen Mirror on the PC.");
    nSDL_DrawString(screen, font, 18, 161, "Touchpad: mouse   Menu: right click");
    nSDL_DrawString(screen, font, 18, 182, "Scratchpad + swipe: scroll");
    nSDL_DrawString(screen, font, 18, 212, "Ctrl+Esc: exit  Flag: stats  Doc: Alt");
    present();
}
static void waiting_for_keyframe(const char *status) {
    /* Avoid clearing the LCD during recovery after playback has started.
     * Direct VRAM decoding may already have written part of the failed frame. */
    if (!have_picture)
        waiting(status);
}
#define CHANNEL_EXPORT(name, number)                                                               \
    static uint32_t export_##name(void) {                                                          \
        unsigned saved = native_interrupt_mask();                                                  \
        register uint32_t address __asm__("r0");                                                   \
        __asm__ volatile("swi %[nr]"                                                               \
                         : "=r"(address)                                                           \
                         : [nr] "i"(__SYSCALLS_ISVAR | (number))                                   \
                         : "r1", "r2", "r3", "r4", "r12", "lr", "memory", "cc");                   \
        uint32_t result = address;                                                                 \
        native_critical_leave(saved);                                                              \
        return result;                                                                             \
    }
CHANNEL_EXPORT(channel_read, e_TI_NN_Read)
CHANNEL_EXPORT(channel_write, e_TI_NN_Write)
CHANNEL_EXPORT(current_task, e_TCC_Current_Task_Pointer)
static bool channel_callable(const PortableView *v, uintptr_t address) {
    uint32_t at = (uint32_t)address & ~1U;
    return v && address && (!(address & 3U) || (address & 1U)) && at >= v->code_begin &&
           at < v->code_end && v->allow_span(v->context, at, 4U, PORTABLE_CODE);
}
static bool channel_api_init(void) {
    unsigned saved = native_critical_enter();
    channel_read_export = export_channel_read();
    channel_write_export = export_channel_write();
    current_task_export = export_current_task();
    native_critical_enter();
    PortableReaderPlatform mapping = {0};
    bool ready = false;
    if (portable_reader_platform_refresh(&mapping)) {
        const PortableView *view = portable_reader_platform_view(&mapping);
        ready = channel_callable(view, channel_read_export) &&
                channel_callable(view, channel_write_export) &&
                channel_callable(view, current_task_export);
        if (ready)
            channel_read_target = mirror_channel_target(view, channel_read_export);
        if (ready) {
#if MIRROR_IDLE_TIMING
            uint32_t table, rom_hash;
            __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(table));
            table &= 0xffffc000U;
            uint32_t sram_mapping = table == 0xa4004000U
                ? ((volatile uint32_t *)(uintptr_t)table)[0xa40] : 0;
            timing_ttbr = table;
            timing_mapping = sram_mapping;
            bool rom_valid = mirror_nnse_hash(view, 0x10000910U, 86U, &rom_hash);
            timing_rom_hash = rom_valid ? rom_hash : 0;
            if (view->allow_span(view->context, 0x10c52e3cU, 4U, PORTABLE_CODE))
                (void)view->read_word(view->context, 0x10c52e3cU, &timing_pointer);
            if (nspire_hardware_id() == 0x202U &&
                (sram_mapping & 0xfff00003U) == 0xa4000002U && (sram_mapping & 0xc00U))
                (void)mirror_timing_hash(&timing_access, &timing_sram_hash);
            idle_timing.supported = rom_valid &&
                rom_hash == MIRROR_IDLE_ORIGINAL &&
                view->allow_span(view->context, 0x10c52e3cU, 4U, PORTABLE_CODE) &&
                mirror_timing_resolve(&timing_access, nspire_hardware_id(), sram_mapping);
#endif
            (void)mirror_nnse_hash(view, 0x10616aa8U, 67U, &nnse_connector_hash);
            (void)mirror_nnse_hash(view, 0x1061a254U, 57U, &nnse_cleanup_hash);
            nnse_abort_target = mirror_nnse_abort_target(view, nspire_hardware_id(),
                                                         channel_write_export, current_task_export);
        }
    }
    native_critical_leave(saved);
    return ready;
}
static int16_t channel_read(nn_ch_t channel, void *input, uint32_t *bytes) {
    unsigned saved = native_interrupt_mask();
    native_critical_leave(0);
    uintptr_t entry = channel_read_target ? channel_read_target : channel_read_export;
    int16_t rc = (int16_t)((uint16_t (*)(nn_ch_t, unsigned, void *, unsigned, uint32_t))entry)(
        channel, channel_read_target ? 100U : 750U, input, packet_limit,
        (uint32_t)(uintptr_t)bytes);
    native_critical_leave(saved);
    return rc;
}
static int16_t channel_write(nn_ch_t channel, void *reply, unsigned bytes) {
    unsigned saved = native_interrupt_mask();
    native_critical_leave(0);
    int16_t rc =
        ((int16_t (*)(nn_ch_t, void *, unsigned))channel_write_export)(channel, reply, bytes);
    native_critical_leave(saved);
    return rc;
}
static void *channel_current_task(void) { return ((void *(*)(void))current_task_export)(); }
/* OS calls occur without holding the queue lock. Decoder work occurs only in main. */
static void service(nn_ch_t channel, void *unused) {
    (void)unused;
    unsigned saved = native_critical_enter();
    if (callbacks || closing) {
        native_critical_leave(saved);
        return;
    }
    ++callbacks;
    callback_task = (uintptr_t)channel_current_task();
    native_critical_leave(saved);
    /* The callback is serialized above. Keep packet storage off the OS task stack. */
    uint8_t *input = service_input, *reply = service_reply;
    /* The peer owns channel closure. Returning immediately after an RPC can
     * race its ECLOSE with the native dispatch wrapper's retirement. */
    bool persistent = true;
    uint32_t started = __wrap_SDL_GetTicks();
    for (unsigned fragments = 0; fragments < 300; ++fragments) {
        if (closing)
            break;
        uint64_t read_started = profile.enabled ? profile_now() : 0;
        uint32_t bytes = 0;
        callback_io_started = __wrap_SDL_GetTicks();
        callback_io = 1;
        int16_t rc = channel_read(channel, input, &bytes);
        callback_io = 0;
        uint32_t read_ms = __wrap_SDL_GetTicks() - callback_io_started;
        if (read_ms > maximum_read_ms)
            maximum_read_ms = read_ms;
        if (profile.enabled)
            profile_add(PROFILE_READ, read_started);
        /* Native timeout is success with zero bytes. Never send an unsolicited
         * BAD reply: keep waiting on a live stream, checking closing each time. */
        if (rc >= 0 && !bytes) {
            if (closing || !persistent || __wrap_SDL_GetTicks() - started > 5000U)
                break;
            --fragments;
            continue;
        }
        MirrorPacket packet;
        unsigned status = MIRROR_BAD;
        saved = native_critical_enter();
        bool valid = rc >= 0 && mirror_parse(input, bytes, packet_limit, &packet);
        if (valid)
            status = closing ? MIRROR_CLOSING : mirror_accept(&queue, &packet);
        if (valid && status == MIRROR_OK && packet.kind == MIRROR_STREAM)
            persistent = true;
        if (valid && status == MIRROR_OK && packet.kind == MIRROR_PAUSE)
            /* Keep the channel serviced until the PC's explicit ECLOSE. The
             * PAUSE acknowledgement confirms queue state, not completion of
             * native dispatch-wrapper/channel retirement. */
            persistent = true;
        if (valid && status == MIRROR_OK && packet.kind == MIRROR_STATS) {
            if (packet.frame == 1)
                profile_start(os_counter, os_hz);
            else if (packet.frame == 2)
                profile_stop();
#if MIRROR_SRAM_TABLES
            else if (packet.frame == 3 || packet.frame == 4)
                mirror_tables_enable(packet.frame == 4);
#endif
        }
        if (profile.enabled && valid && status == MIRROR_OK && packet.kind == MIRROR_FRAME_DATA &&
            (packet.flags & MIRROR_LAST)) {
            if (frame_active)
                ++profile.rx_active;
            if (decode_cpu)
                ++profile.rx_cpu;
            profile.callback_task = (uint32_t)(uintptr_t)channel_current_task();
            unsigned used = MIRROR_SLOTS - mirror_credit(&queue);
            if (used > profile.max_used)
                profile.max_used = used;
        }
        if (valid && status == MIRROR_OK) {
            if (packet.kind == MIRROR_HELLO || packet.kind == MIRROR_RESET ||
                packet.kind == MIRROR_PAUSE) {
                input_reset(&input_queue, false);
                profile_stop();
                mirror_display_rate_reset(&display_rate);
            } else if (packet.kind == MIRROR_INPUT) {
                input_reset(&input_queue, packet.frame != 0);
                input_push(&input_queue, INPUT_RELEASE, 0, 0, 0, 0);
            }
        }
        if (valid && packet.session == queue.session && packet.kind != MIRROR_HELLO &&
            packet.kind != MIRROR_RESET && packet.kind != MIRROR_INPUT &&
            packet.kind != MIRROR_PAUSE && !input_acknowledge(&input_queue, packet.input_ack))
            status = MIRROR_BAD;
        if (valid && status == MIRROR_OK && packet.kind == MIRROR_CLOCK) {
            if (mirror_credit(&queue) != MIRROR_SLOTS)
                status = MIRROR_BUSY;
            else if (!performance_clock_valid_mhz(packet.frame))
                status = MIRROR_BAD;
            else
                clock_pending = (int)packet.frame;
        }
        if (valid && packet.kind == MIRROR_CAPS && clock_pending >= 0)
            status = MIRROR_BUSY;
        if (valid && status == MIRROR_OK && packet.kind == MIRROR_DISPLAY_RATE)
            mirror_display_rate_update(&display_rate, packet.frame);
        mirror_reply(reply, &queue, status, packet_limit);
        if (valid && packet.kind == MIRROR_CAPS) {
            wire_put(reply + 24, capabilities());
            wire_put(reply + 28, current_mhz());
        }
        unsigned reply_bytes = input_reply(&input_queue, reply);
        if (valid && packet.kind == MIRROR_STATS && status == MIRROR_OK)
            reply_bytes = profile_reply(reply);
        native_critical_leave(saved);
        if (rc < 0) {
            transport_error = rc;
            break;
        }
        bool more = valid && status == MIRROR_OK &&
                    (packet.kind == MIRROR_FRAME_DATA || packet.kind == MIRROR_PROBE) &&
                    !(packet.flags & MIRROR_LAST);
        if (!(more && (packet.flags & MIRROR_BULK))) {
            uint64_t written = profile.enabled ? profile_now() : 0;
            callback_io_started = __wrap_SDL_GetTicks();
            callback_io = 2;
            rc = channel_write(channel, reply, reply_bytes);
            callback_io = 0;
            uint32_t write_ms = __wrap_SDL_GetTicks() - callback_io_started;
            if (write_ms > maximum_write_ms)
                maximum_write_ms = write_ms;
            if (profile.enabled)
                profile_add(PROFILE_WRITE, written);
            if (rc < 0) {
                transport_error = rc;
                break;
            }
        }
        transport_error = 0;
        if (status != MIRROR_OK && status != MIRROR_RESYNC)
            break;
        if (!more && !persistent)
            break;
        if (persistent && !more) {
            fragments = 0;
            started = __wrap_SDL_GetTicks();
        }
        if (__wrap_SDL_GetTicks() - started > 5000U)
            break;
    }
    saved = native_critical_enter();
    if (queue.assembling >= 0) {
        queue.assembling = -1;
        queue.need_key = true;
    }
    --callbacks;
    native_critical_leave(saved);
}
static bool service_stop_step(void) {
    if (callbacks)
        return false;
    /* Our callback counter drops before the native dispatch wrapper returns.
     * Yield first so its channel references can be released before deletion. */
    for (unsigned tick = 0; tick < 2U; ++tick) {
        (void)native_usb_ui_wait();
        native_critical_enter();
        ++close_waits;
        if (callbacks)
            return false;
    }
    if (!performance_clock_restore()) {
        close_result = -1;
        write_log("clock_restore_failed", 1);
        return false;
    }
#if MIRROR_IDLE_TIMING
    (void)timing_release();
#endif
    write_log("before_native_service_stop", 0);
    native_critical_leave(0);
    int16_t stopped = TI_NN_StopService(MIRROR_SERVICE);
    native_critical_enter();
    close_result = stopped;
    return stopped >= 0 && !callbacks;
}
static void service_abort_detached(void) {
    if (!nnse_abort_target || nnse_abort_attempts || !closing)
        return;
    nnse_ws_mode = *(const volatile uint32_t *)0x113e1bb4U;
    /* A disconnected/stalled native transport can hang inside this cleanup.
     * Keep its live-mode guard; callback retirement is handled by peer close. */
    if (!nnse_ws_mode) {
        nnse_detached_seen = false;
        return;
    }
    nnse_capability = *(const volatile uint32_t *)0xb0000000U;
    nnse_otg = *(const volatile uint32_t *)0xb0000080U;
    if (!MIRROR_FORCE_NATIVE_ABORT &&
        !mirror_nnse_shutdown_link(nspire_hardware_id(), nnse_capability, nnse_otg, closing)) {
        nnse_detached_seen = false;
        return;
    }
    uint32_t now = __wrap_SDL_GetTicks();
    if (!nnse_detached_seen) {
        nnse_detached_since = now;
        nnse_detached_seen = true;
        return;
    }
    if (now - nnse_detached_since < 50U)
        return;
    write_log("before_native_nnse_abort", 0);
    /* Logging may yield to the native writer/disconnect handler. Recheck the
     * active wait and physical session before initiating driver cleanup. */
    nnse_capability = *(const volatile uint32_t *)0xb0000000U;
    nnse_otg = *(const volatile uint32_t *)0xb0000080U;
    if (!closing ||
        (!MIRROR_FORCE_NATIVE_ABORT &&
         !mirror_nnse_shutdown_link(nspire_hardware_id(), nnse_capability, nnse_otg, closing)))
        return;
    ++nnse_abort_attempts;
    /* The verified native disconnect routine wakes its own waiters and waits
     * for them to release their packet references. Its waits require IRQs. */
    unsigned saved = native_interrupt_mask();
    native_critical_leave(0);
    nnse_abort_result = ((int (*)(int))nnse_abort_target)(0);
    native_critical_leave(saved);
    write_log("after_native_nnse_abort", 0);
}
static void capture_worker_wait(void) {
    unsigned saved = native_critical_enter();
    unsigned io = callback_io;
    uint32_t elapsed = io ? __wrap_SDL_GetTicks() - callback_io_started : 0;
    if (callbacks && io && elapsed >= 200U && elapsed > worker_wait_ms) {
        PortableReaderPlatform mapping = {0};
        if (portable_reader_platform_refresh(&mapping) &&
            mirror_worker_wait(portable_reader_platform_view(&mapping), (uint32_t)callback_task,
                               &longest_worker_wait)) {
            worker_wait_ms = elapsed;
            worker_wait_io = io;
        }
    }
    native_critical_leave(saved);
}
static void write_log(const char *stage, int result) {
    uint32_t rtc = (uint32_t)time(NULL), ticks = __wrap_SDL_GetTicks();
    mirror_log_record(&log_history, (MirrorLogEntry){
        stage, rtc, ticks, queue.session, queue.received, queue.displayed,
        current_mhz(), callbacks, callback_io, result});
    if (!mirror_log_enabled(&log_enabled, stage))
        return;
    capture_worker_wait();
    char path[1024];
    const char *program = program_path;
    const char *slash = program ? strrchr(program, '/') : NULL;
    if (slash && slash - program < 900)
        snprintf(path, sizeof(path), "%.*s/ndmirror.log.tns", (int)(slash - program), program);
    else
        strcpy(path, "ndmirror.log.tns");
    FILE *log = fopen(path, "wb");
    if (log) {
        fprintf(log,
                "ND Screen Mirror | %s %s\nasic=%08lx packet_limit=%u received=%lu "
                "displayed=%lu rejected=%lu usb_error=%d result=%d\nclock_hz=%lu notice=%s\n",
                __DATE__, __TIME__, (unsigned long)nspire_hardware_id(), packet_limit,
                (unsigned long)queue.received, (unsigned long)queue.displayed,
                (unsigned long)queue.rejected, transport_error, result, (unsigned long)os_hz,
                notice);
        fprintf(log, "diagnostic_identity rtc=%lu ticks=%lu session=%lu\n",
                (unsigned long)rtc, (unsigned long)ticks, (unsigned long)queue.session);
        mirror_log_print(log, &log_history);
#if MIRROR_IDLE_TIMING
        uint32_t idle_hash = 0;
        if (idle_timing.supported)
            (void)mirror_timing_hash(&timing_access, &idle_hash);
        fprintf(log, "idle_timing supported=%u held=%u holds=%u restores=%u revocations=%u "
                "status=%d image_fnv32=%08lx\n", idle_timing.supported, idle_timing.held,
                idle_timing.holds, idle_timing.restores, idle_timing.revocations,
                idle_timing.status, (unsigned long)idle_hash);
        fprintf(log, "idle_timing_witness ttbr=%08lx mapping=%08lx pointer=%08lx "
                "rom_fnv32=%08lx sram_fnv32=%08lx\n", (unsigned long)timing_ttbr,
                (unsigned long)timing_mapping, (unsigned long)timing_pointer,
                (unsigned long)timing_rom_hash, (unsigned long)timing_sram_hash);
        if (!idle_timing.supported && timing_sram_hash) {
            for (unsigned i = 0; i < 86U; ++i) {
                uint32_t word = 0;
                (void)timing_read(NULL, MIRROR_IDLE_ENTRY + i * 4U, &word);
                fprintf(log, "idle_timing_word index=%u value=%08lx\n", i, (unsigned long)word);
            }
        }
#endif
        fprintf(log, "diagnostic_stage=%s callbacks=%u closing=%u close_waits=%u stop_result=%d\n",
                stage, callbacks, closing, close_waits, close_result);
        fprintf(log, "channel_read_export=%08lx target=%08lx timeout_ms=%u path=%s\n",
                (unsigned long)channel_read_export, (unsigned long)channel_read_target,
                channel_read_target ? 100U : 750U,
                channel_read_target ? "verified_body" : "public_timeout_unverified");
        fprintf(log, "channel_callback_gateway=direct write_export=%08lx task_export=%08lx\n",
                (unsigned long)channel_write_export, (unsigned long)current_task_export);
        fprintf(log,
                "channel_write_path=native_export nominal_timeout_ms=62000 "
                "callback_io=%u io_elapsed_ms=%lu max_read_ms=%lu max_write_ms=%lu\n",
                callback_io,
                (unsigned long)(callback_io ? __wrap_SDL_GetTicks() - callback_io_started : 0),
                (unsigned long)maximum_read_ms, (unsigned long)maximum_write_ms);
        fprintf(log,
                "worker_wait_captured=%u io=%u elapsed_ms=%lu task=%08lx "
                "state=%08lx stack=%08lx/%08lx saved_sp=%08lx cleanup=%08lx wait_info=%08lx\n",
                longest_worker_wait.count != 0, worker_wait_io, (unsigned long)worker_wait_ms,
                (unsigned long)longest_worker_wait.task, (unsigned long)longest_worker_wait.state,
                (unsigned long)longest_worker_wait.stack_begin,
                (unsigned long)longest_worker_wait.stack_end,
                (unsigned long)longest_worker_wait.saved_sp,
                (unsigned long)longest_worker_wait.cleanup,
                (unsigned long)longest_worker_wait.wait_info);
        fprintf(log,
                "native_nnse_abort supported=%u target=%08lx attempts=%u result=%d force_test=%u "
                "capability=%08lx otg=%08lx\n",
                nnse_abort_target != 0, (unsigned long)nnse_abort_target, nnse_abort_attempts,
                nnse_abort_result, MIRROR_FORCE_NATIVE_ABORT, (unsigned long)nnse_capability,
                (unsigned long)nnse_otg);
        fprintf(log, "native_nnse_profile connector_hash=%08lx cleanup_hash=%08lx ws_mode=%08lx\n",
                (unsigned long)nnse_connector_hash, (unsigned long)nnse_cleanup_hash,
                (unsigned long)nnse_ws_mode);
        for (unsigned i = 0; i < longest_worker_wait.count; i += 8U) {
            fprintf(log,
                    "worker_stack %08lx:", (unsigned long)(longest_worker_wait.saved_sp + i * 4U));
            for (unsigned j = i; j < i + 8U && j < longest_worker_wait.count; ++j)
                fprintf(log, " %08lx", (unsigned long)longest_worker_wait.words[j]);
            fputc('\n', log);
        }
        native_usb_ui_debug(log);
        performance_clock_debug(log);
#if MIRROR_SRAM_TABLES
        mirror_tables_debug(log);
#endif
        fprintf(log,
                "display_direct_build=%u direct_frames=%lu buffered_frames=%lu "
                "buffered_when=overlay_nonh264_or_unverified_mapping\n",
                MIRROR_DIRECT_VRAM, (unsigned long)direct_presented,
                (unsigned long)buffered_presented);
        fprintf(log, "decode_failures=%u retained=%u\n", decode_failure_count,
                decode_failure_count < 8U ? decode_failure_count : 8U);
        unsigned count = decode_failure_count < 8U ? decode_failure_count : 8U;
        for (unsigned entry = 0; entry < count; ++entry) {
            unsigned index = (decode_failure_count - count + entry) % 8U;
            fprintf(log,
                    "decode_failure frame=%lu bytes=%lu offset=%lu code=%lu MHz=%lu stage=%s "
                    "reason=%s detail=%lu bit=%lu\n",
                    (unsigned long)decode_failures[index].frame,
                    (unsigned long)decode_failures[index].bytes,
                    (unsigned long)decode_failures[index].offset,
                    (unsigned long)decode_failures[index].code,
                    (unsigned long)decode_failures[index].mhz, decode_failures[index].stage,
                    decode_failures[index].reason, (unsigned long)decode_failures[index].detail,
                    (unsigned long)decode_failures[index].bit);
            fprintf(log,
                    "resume mb=%lu skip=%lu qp=%ld bit_before=%lu already_decoded=%lu "
                    "pointer_matches=%lu\n",
                    (unsigned long)decode_failures[index].resume_mb,
                    (unsigned long)decode_failures[index].resume_skip,
                    (long)decode_failures[index].resume_qp,
                    (unsigned long)decode_failures[index].resume_bit,
                    (unsigned long)decode_failures[index].resume_decoded,
                    (unsigned long)decode_failures[index].resume_pointer);
            fprintf(log, "input_fnv32=%08lx\n", (unsigned long)decode_failures[index].input_hash);
        }
#if MIRROR_CAPTURE_INPUT
        fprintf(log, "decode_input session=%lu frame=%lu bytes=%lu input_fnv32=%08lx "
                "saved=%u saved_frame=%lu saved_fnv32=%08lx file=ndmirror-frame.bin.tns\n",
                (unsigned long)decode_input.session, (unsigned long)decode_input.frame,
                (unsigned long)decode_input.bytes, (unsigned long)decode_input.hash,
                decode_input_saved, (unsigned long)decode_input_saved_frame,
                (unsigned long)decode_input_saved_hash);
        fprintf(log, "decode_input_post_fnv32=%08lx\n",
                (unsigned long)decode_input_saved_post_hash);
        fprintf(log, "decode_input_clock pll=%08lx emi74=%08lx emi1c=%08lx idle_timing_literal=%08lx "
                "applied_pll=%08lx applied_emi74=%08lx applied_emi1c=%08lx\n",
                (unsigned long)decode_input.pll, (unsigned long)decode_input.emi74,
                (unsigned long)decode_input.emi1c, (unsigned long)decode_input.idle_literal,
                (unsigned long)applied_pll,
                (unsigned long)applied_emi74, (unsigned long)applied_emi1c);
        fprintf(log, "decode_timing checked=%lu mismatched=%lu first_frame=%lu first_emi1c=%08lx\n",
                (unsigned long)timing_checked, (unsigned long)timing_mismatched,
                (unsigned long)timing_first_frame, (unsigned long)timing_first_emi1c);
#endif
#if MIRROR_DECODE_GUARD
        fprintf(log, "decode_guard checks=%lu changes=%lu frame=%lu mb=%lu bit=%lu mask=%lu\n",
                (unsigned long)decode_guard.checks, (unsigned long)decode_guard.changes,
                (unsigned long)decode_guard.frame, (unsigned long)decode_guard.mb,
                (unsigned long)decode_guard.bit, (unsigned long)decode_guard.mask);
        for (unsigned i = 0; i < MIRROR_GUARD_REGIONS; ++i) {
            const MirrorGuardRegion *r = &decode_guard.region[i];
            fprintf(log, "guard_region=%u address=%08lx bytes=%u before=%08lx after=%08lx\n",
                    i, (unsigned long)(uintptr_t)r->address, r->bytes,
                    (unsigned long)r->before, (unsigned long)r->after);
        }
#endif
        bool saved = fclose(log) == 0;
        if (saved && (!strcmp(stage, "manual_save") || !strcmp(stage, "decode_failure") ||
                      !strcmp(stage, "decode_guard_changed"))) {
            char event_path[1024];
            if (slash && slash - program < 900)
                snprintf(event_path, sizeof(event_path), "%.*s/ndmirror-event.log.tns",
                         (int)(slash - program), program);
            else
                strcpy(event_path, "ndmirror-event.log.tns");
            (void)mirror_log_copy(path, event_path);
        }
    }
}

int main(int argc, char **argv) {
    unsigned entry = native_critical_enter(), policy = 0;
    bool policy_owned = false, started = false;
    int result = 0;
    program_path = argc > 0 ? argv[0] : NULL;
    MirrorDecoder decoder = {0};
    void *mpeg4 = NULL;
    int active = -1;
    bool stats = false, previous_flag = false, previous_esc = true;
    uint32_t frames = 0;
    bool previous_d = false, paused_drawn = false;
    mirror_queue_init(&queue);
    nspire_hardware_init();
    packet_limit = nspire_is_cx2() ? 1440U : 254U;
    if (MIRROR_SRAM_TABLES && !nspire_is_cx2()) {
        snprintf(notice, sizeof(notice), "This SRAM experiment requires a CX II.");
        result = 1;
        goto done;
    }
    if (!has_colors || !nl_hassyscall(TI_NN_StartService) || !nl_hassyscall(TI_NN_StopService) ||
        !nl_hassyscall(TI_NN_Read) || !nl_hassyscall(TI_NN_Write)) {
        snprintf(notice, sizeof(notice), "This model lacks the required USB services.");
        result = 1;
        goto done;
    }
    if (!native_usb_ui_clock(&os_counter, &os_hz, NULL, NULL)) {
        snprintf(notice, sizeof(notice), "Could not discover the native display/USB interface.");
        result = 1;
        goto done;
    }
    clock_start = *os_counter;
    profile_bind_main();
    policy = TCT_Local_Control_Interrupts(0);
    policy_owned = true;
    native_critical_enter();
    result = native_usb_ui_acquire(NULL, NULL);
    if (result) {
        snprintf(notice, sizeof(notice), "Could not reserve display (%d).", result);
        goto done;
    }
    if (SDL_Init(SDL_INIT_VIDEO) != 0 ||
        (screen = SDL_SetVideoMode(320, 240, 16, SDL_SWSURFACE)) == NULL) {
        snprintf(notice, sizeof(notice), "Could not initialize display.");
        result = 1;
        goto done;
    }
    font = nSDL_LoadFont(NSDL_FONT_TINYTYPE, 235, 235, 235);
    if (!font) {
        snprintf(notice, sizeof(notice), "Could not allocate display resources.");
        result = 1;
        goto done;
    }
#if MIRROR_SRAM_TABLES
    mirror_tables_init(os_hz);
#endif
    waiting("Waiting for the PC...");
    if (!channel_api_init()) {
        snprintf(notice, sizeof(notice), "Could not validate the native USB calls.");
        result = 1;
        goto done;
    }
    write_log("service_starting", result);
    int16_t rc = TI_NN_StartService(MIRROR_SERVICE, NULL, service);
    native_critical_enter();
    if (rc < 0) {
        snprintf(notice, sizeof(notice), "Could not start USB service (%d).", rc);
        result = 1;
        goto done;
    }
    started = true;
    for (;;) {
        bool esc = isKeyPressed(KEY_NSPIRE_ESC) != 0;
        bool local = !input_queue.enabled || isKeyPressed(KEY_NSPIRE_CTRL);
        bool exit_chord = esc && local;
        if (exit_chord && !previous_esc)
            break;
        previous_esc = exit_chord;
        bool flag = isKeyPressed(KEY_NSPIRE_FLAG) != 0;
        if (flag && !previous_flag)
            stats = !stats;
        previous_flag = flag;
        bool d = isKeyPressed(KEY_NSPIRE_D) != 0;
        bool log_chord = d && local;
        if (log_chord && !previous_d) {
            save_log = true;
            write_log("manual_save", result);
        }
        previous_d = log_chord;
        uint64_t input_started = profile.enabled ? profile_now() : 0;
        mirror_input_scan(&input_queue, __wrap_SDL_GetTicks(), local);
        if (profile.enabled)
            profile_add(PROFILE_INPUT, input_started);
        if (queue.reset && active < 0) {
            write_log("decoder_reset_begin", result);
            mirror_pixels_reset();
            mirror_decoder_free(&decoder);
            write_log("h264_reset_done", result);
            mpeg4_xvid_destroy(mpeg4);
            mpeg4 = NULL;
            queue.reset = false;
            frames = 0;
            mirror_display_rate_reset(&display_rate);
            write_log("decoder_reset_done", result);
        }
        if (queue.paused && active < 0 && !paused_drawn) {
            waiting("Mirroring paused. Start on the PC.");
            paused_drawn = true;
        }
        if (!queue.paused)
            paused_drawn = false;
        if (clock_pending >= 0 && !callbacks && active < 0 &&
            mirror_credit(&queue) == MIRROR_SLOTS) {
            unsigned requested = (unsigned)clock_pending;
            bool policy_ok = true;
#if MIRROR_IDLE_TIMING
            bool held_before = idle_timing.held;
            bool overclock = nspire_is_cx2() && requested > MIRROR_IDLE_BASELINE_MHZ;
            if (overclock)
                policy_ok = mirror_timing_hold(&idle_timing, &timing_access);
#endif
            bool clock_ok = policy_ok && (requested ? performance_clock_choose(requested, false)
                                      : performance_clock_restore());
#if MIRROR_IDLE_TIMING
            (void)mirror_timing_after_clock(&idle_timing, &timing_access, overclock, clock_ok,
                                          held_before, current_mhz());
#endif
            clock_pending = -1;
#if MIRROR_CAPTURE_INPUT
            if (clock_ok && nspire_is_cx2()) {
                applied_pll = *(volatile uint32_t *)0x90140030U;
                applied_emi74 = *(volatile uint32_t *)0x90120074U;
                applied_emi1c = *(volatile uint32_t *)0x9012001cU;
                timing_checked = timing_mismatched = timing_first_frame = timing_first_emi1c = 0;
            }
#endif
            if (!clock_ok) {
                snprintf(notice, sizeof(notice), "Clock change failed (%d).",
                         performance_clock_status());
                save_log = true;
            }
        }
        if (active < 0)
            active = mirror_take(&queue);
        frame_active = active >= 0;
        if (active >= 0) {
            MirrorFrame *frame = &queue.frame[active];
            if (!(frame->flags & (MIRROR_RAW | MIRROR_DELTA | MIRROR_MPEG4)) && !decoder.storage &&
                !mirror_decoder_init(&decoder)) {
                strcpy(notice, "Decoder allocation failed.");
                result = 1;
                break;
            }
            if ((frame->flags & MIRROR_MPEG4) && !mpeg4 &&
                (!mpeg4_xvid_global_init(NULL, 0) || !mpeg4_xvid_create(&mpeg4, 320, 240))) {
                strcpy(notice, "MPEG-4 decoder allocation failed.");
                result = 1;
                break;
            }
            uint64_t decode_started = profile.enabled ? profile_now() : 0;
            bool is_h264 = !(frame->flags & (MIRROR_RAW | MIRROR_DELTA | MIRROR_MPEG4));
#if MIRROR_CAPTURE_INPUT
            if (is_h264 && decoder.sequence != frame->id) {
                (void)mirror_input_capture(&decode_input, queue.session, frame);
                decode_input.idle_literal = 0U;
                if (nspire_is_cx2()) {
                    decode_input.pll = *(volatile uint32_t *)0x90140030U;
                    decode_input.emi74 = *(volatile uint32_t *)0x90120074U;
                    decode_input.emi1c = *(volatile uint32_t *)0x9012001cU;
#if MIRROR_IDLE_TIMING
                    if (idle_timing.supported)
                        (void)timing_read(NULL, MIRROR_IDLE_LITERAL, &decode_input.idle_literal);
#endif
                    if (applied_pll) {
                        ++timing_checked;
                        if (decode_input.pll != applied_pll || decode_input.emi74 != applied_emi74 ||
                            decode_input.emi1c != applied_emi1c) {
                            if (!timing_mismatched) {
                                timing_first_frame = frame->id;
                                timing_first_emi1c = decode_input.emi1c;
                            }
                            ++timing_mismatched;
                        }
                    }
                }

            }
#endif
            uint16_t *target = frame_target(stats, is_h264);
            bool direct = target != screen->pixels;
            decode_cpu = true;
            int decoded =
                frame->flags & (MIRROR_RAW | MIRROR_DELTA)
                    ? (mirror_pixels_decode(frame, screen->pixels, screen->pitch / 2U) ? 1 : -1)
                : frame->flags & MIRROR_MPEG4
                    ? (mpeg4_xvid_decode_frame(mpeg4, frame->data, frame->total, screen->pixels,
                                               320, 240, true, frame->id == 1)
                           ? 1
                           : -1)
                    : mirror_decoder_step(&decoder, frame->data, frame->total, frame->id, target,
                                          screen->pitch / 2U);
            decode_cpu = false;
            if (profile.enabled) {
                ++profile.calls;
                profile_add(PROFILE_DECODE, decode_started);
            }
            if (decoded) {
                if (decoded > 0) {
                    queue.displayed = frame->id;
                    have_picture = true;
                    ++frames;
                    if (stats) {
                        SDL_Rect box = {0, 0, 320, 14};
                        SDL_FillRect(screen, &box, 0x1082);
                        const char *codec = frame->flags & (MIRROR_RAW | MIRROR_DELTA) ? "RGB565"
                                            : frame->flags & MIRROR_MPEG4              ? "MPEG4"
                                                                                       : "H264";
                        if (display_rate.valid)
                            nSDL_DrawString(screen, font, 4, 3, "%s %lu.%lu FPS %u MHz", codec,
                                            display_rate.tenths / 10U, display_rate.tenths % 10U,
                                            current_mhz());
                        else
                            nSDL_DrawString(screen, font, 4, 3, "%s --.- FPS %u MHz", codec,
                                            current_mhz());
                    }
                    uint64_t blit_started = profile.enabled ? profile_now() : 0;
                    if (direct)
                        ++direct_presented;
                    else {
                        present();
                        ++buffered_presented;
                    }
                    if (profile.enabled) {
                        profile_add(PROFILE_BLIT, blit_started);
                        ++profile.frames;
                    }
                } else {
                    unsigned index = decode_failure_count++ % 8U;
                    memset(&decode_failures[index], 0, sizeof(decode_failures[index]));
                    decode_failures[index].frame = frame->id;
                    decode_failures[index].bytes = frame->total;
                    decode_failures[index].offset = decoder.offset;
                    decode_failures[index].code = decoder.last_result;
                    decode_failures[index].mhz = current_mhz();
                    decode_failures[index].resume_mb = decoder.resume_mb;
                    decode_failures[index].resume_skip = decoder.resume_skip;
                    decode_failures[index].resume_bit = decoder.resume_bit;
                    decode_failures[index].resume_decoded = decoder.resume_decoded;
                    decode_failures[index].resume_pointer = decoder.resume_pointer;
                    decode_failures[index].resume_qp = decoder.resume_qp;
                    decode_failures[index].input_hash = decoder.input_hash;
                    bool h264 = !(frame->flags & (MIRROR_RAW | MIRROR_DELTA | MIRROR_MPEG4));
                    snprintf(
                        decode_failures[index].stage, sizeof(decode_failures[index].stage), "%s",
                        h264 && decoder.failure ? decoder.failure : "pixel/MPEG4 decode failure");
                    snprintf(decode_failures[index].reason, sizeof(decode_failures[index].reason),
                             "%s",
                             h264 && decoder.storage && decoder.storage->errorReason
                                 ? decoder.storage->errorReason
                                 : "none");
                    if (h264 && decoder.storage) {
                        decode_failures[index].detail = decoder.storage->errorDetail;
                        decode_failures[index].bit = decoder.storage->errorBit;
                    }
                    save_log = true;
                    mirror_queue_recover(&queue);
#if MIRROR_CAPTURE_INPUT
                    if (h264 && decode_input.session == queue.session &&
                        decode_input.frame == frame->id && decode_input.bytes == frame->total) {
                        /* Hash the current decoder buffer before filesystem calls.
                         * It can differ normally when NAL unescaping removed bytes. */
                        decode_input_saved_post_hash = mirror_input_hash(frame->data, frame->total);
                        char input_path[1024];
                        const char *slash = program_path ? strrchr(program_path, '/') : NULL;
                        if (slash && slash - program_path < 900)
                            snprintf(input_path, sizeof(input_path), "%.*s/ndmirror-frame.bin.tns",
                                     (int)(slash - program_path), program_path);
                        else
                            strcpy(input_path, "ndmirror-frame.bin.tns");
                        decode_input_saved = mirror_input_save(&decode_input, input_path);
                        decode_input_saved_frame = decode_input.frame;
                        decode_input_saved_hash = decode_input.hash;
                    }
#endif
                    write_log("decode_failure", result);
                    waiting_for_keyframe("Stream reset needed. Reconnecting...");
                }
                frame->busy = false;
                active = -1;
                frame_active = false;
            }
        }
        /* Yield the OS task at a frame boundary, not once per 32 macroblocks.
         * Otherwise a 100 Hz OS tick adds ~100 ms of artificial delay/frame. */
        bool poll = active >= 0;
#if MIRROR_DECODE_GUARD
        decode_guard.armed = false;
        if (poll && !(queue.frame[active].flags & (MIRROR_RAW | MIRROR_DELTA | MIRROR_MPEG4)))
            (void)mirror_guard_begin(&decode_guard, &decoder, &queue.frame[active]);
#endif
        bool wait_measured = profile.enabled;
        uint64_t wait_started = wait_measured ? profile_now() : 0;
        result = poll ? native_usb_ui_poll() : native_usb_ui_wait();
        native_critical_enter();
        if (profile.enabled && wait_measured)
            profile_add(poll ? PROFILE_POLL : PROFILE_SLEEP, wait_started);
        if (result) {
            snprintf(notice, sizeof(notice), "USB scheduler wait failed (%d).", result);
            break;
        }
        result = native_usb_ui_reassert();
#if MIRROR_DECODE_GUARD
        if (mirror_guard_end(&decode_guard)) {
            /* A changed pointer may be unsafe even for decoder cleanup. Preserve
             * the evidence, close USB normally and leave these allocations alone. */
            result = 1;
            snprintf(notice, sizeof(notice), "Decoder state changed during USB scheduling.");
            write_log("decode_guard_changed", result);
            queue.frame[active].busy = false;
            active = -1;
            frame_active = false;
            break;
        }
#endif
        if (result) {
            snprintf(notice, sizeof(notice), "Display ownership failed (%d).", result);
            break;
        }
    }
done:
    native_critical_enter();
    closing = true;
    if (started && screen && font)
        waiting("Closing the receiver...");
    write_log("exit_requested", result);
    uint32_t close_started = __wrap_SDL_GetTicks();
    bool wait_logged = false;
    /* A failed StopService must not leave a callback pointing into an unloaded app. */
    while (started) {
        service_abort_detached();
        /* Give logical connector cleanup its debounce window before entering a
         * native service deletion that can wait indefinitely for wrapper refs. */
        if (nnse_detached_seen && !nnse_abort_attempts) {
            (void)native_usb_ui_wait();
            native_critical_enter();
            continue;
        }
        /* StopService may wait for its task: let the callback return first,
         * and keep native IRQs enabled during the service's own teardown. */
        if (service_stop_step()) {
            started = false;
            break;
        }
        if (screen && font)
            waiting("Close the PC sender; waiting for USB...");
        (void)native_usb_ui_wait();
        native_critical_enter();
        if (!wait_logged && __wrap_SDL_GetTicks() - close_started >= 1000U) {
            write_log("usb_drain_wait", result);
            wait_logged = true;
        }
    }
    write_log("service_stopped", result);
#if MIRROR_DECODE_GUARD
    if (!decode_guard.changes)
#endif
    mirror_decoder_free(&decoder);
    mpeg4_xvid_destroy(mpeg4);
    if (font)
        nSDL_FreeFont(font);
    SDL_Quit();
    native_critical_enter();

    int cleanup = native_usb_ui_release();
    while (cleanup) {
        native_critical_leave(0);
        show_msgbox("ND Screen Mirror", "Could not restore display. Press OK to retry.");
        native_critical_enter();
        cleanup = native_usb_ui_release();
    }
    if (!performance_clock_finish(result == 0)) {
        strcpy(notice, "Could not restore the entry clock.");
        result = 1;
    }
#if MIRROR_IDLE_TIMING
    if (!timing_release()) {
        snprintf(notice, sizeof(notice), "Native idle timing restoration could not be verified.");
        result = 1;
    }
    write_log("idle_timing_final", result);
#endif
    if (policy_owned)
        TCT_Local_Control_Interrupts(policy);
    native_critical_leave(entry);
    if (notice[0])
        show_msgbox("ND Screen Mirror", notice);
    return result ? 1 : 0;
}
