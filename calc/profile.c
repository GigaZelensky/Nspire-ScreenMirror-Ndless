#include <os.h>
#include <string.h>
#include "profile.h"
#include "wire.h"
#include "sram_tables.h"
MirrorProfile profile;
static uint32_t foreground_task;
#ifndef REG
#define REG(at) (*(const volatile uint32_t *)(uintptr_t)(at))
#endif
void profile_bind_main(void) { foreground_task = (uint32_t)(uintptr_t)TCC_Current_Task_Pointer(); }
uint64_t profile_now(void) {
    if (!profile.counter)
        return 0;
    if (!profile.high_resolution)
        return *profile.counter;
    uint32_t ticks = *profile.counter, value = REG(profile.timer_base + 4U),
             pending = REG(profile.timer_base + 16U) & 1U;
    if (value >= profile.period) {
        ++profile.bad_clock;
        value = profile.period - 1U;
    }
    /* The native periodic timer can expire while foreground IRQs are masked.
     * Its pending bit accounts for that tick before the OS handler increments it.
     * Neither the counter nor any timer register is modified. */
    return ((uint64_t)ticks + pending) * profile.period + (profile.period - 1U - value);
}
void profile_start(const volatile uint32_t *counter, uint32_t hz) {
    memset(&profile, 0, sizeof(profile));
    profile.counter = counter;
    profile.os_hz = hz;
    profile.hz = hz;
    static const uint32_t bases[] = {0x900c0000U, 0x900c0020U, 0x900d0000U, 0x900d0020U};
    for (unsigned i = 0; i < 4; ++i) {
        uint32_t control = REG(bases[i] + 8U), load = REG(bases[i]);
        if ((control & 0xe2U) == 0xe2U && load && load < UINT32_MAX &&
            (uint64_t)(load + 1U) * hz <= UINT32_MAX) {
            profile.high_resolution = true;
            profile.timer_base = bases[i];
            profile.control = control;
            profile.period = load + 1U;
            profile.hz = profile.period * hz;
            break;
        }
    }
    profile.main_task = foreground_task;
    profile.enabled = true;
    profile.started = profile_now();
}
void profile_stop(void) {
    if (profile.enabled)
        profile.stopped = profile_now();
    profile.enabled = false;
}
void profile_add(unsigned stage, uint64_t start) {
    if (!profile.enabled || stage >= PROFILE_STAGES)
        return;
    uint64_t now = profile_now();
    if (now < start || now - start > (uint64_t)profile.hz * 5U) {
        ++profile.bad_clock;
        return;
    }
    profile.time[stage] += now - start;
}
unsigned profile_reply(uint8_t *reply) {
    uint32_t words[MIRROR_PROFILE_WORDS] = {profile.enabled | (profile.high_resolution ? 2U : 0U),
                                            profile.hz,
                                            profile.os_hz,
                                            profile.period,
                                            profile.control,
                                            profile.frames,
                                            profile.calls,
                                            profile.rx_active,
                                            profile.rx_cpu,
                                            profile.max_used,
                                            profile.main_task,
                                            profile.callback_task,
                                            profile.bad_clock};
    for (unsigned i = 0; i < PROFILE_STAGES; ++i) {
        words[13 + i * 2] = (uint32_t)profile.time[i];
        words[14 + i * 2] = (uint32_t)(profile.time[i] >> 32);
    }
    uint64_t now = profile.stopped ? profile.stopped : profile_now(),
             elapsed = now >= profile.started ? now - profile.started : 0;
    words[27] = (uint32_t)elapsed;
    words[28] = (uint32_t)(elapsed >> 32);
    words[29] = profile.timer_base;
    static const uint32_t bases[] = {0x900c0000U, 0x900c0020U, 0x900d0000U, 0x900d0020U};
    for (unsigned i = 0; i < 4; ++i)
        for (unsigned j = 0; j < 4; ++j)
            words[30 + i * 4 + j] = REG(bases[i] + (j == 3 ? 16U : j * 4U));
    words[46] = REG(0x900c0080U);
    words[47] = REG(0x900d0080U);
#if MIRROR_SRAM_TABLES
    mirror_tables_words(words + 48);
#endif
    memcpy(reply + 32, "NDS1", 4);
    wire_put(reply + 36, MIRROR_PROFILE_WORDS);
    for (unsigned i = 0; i < MIRROR_PROFILE_WORDS; ++i)
        wire_put(reply + 40 + i * 4, words[i]);
    return 40U + MIRROR_PROFILE_WORDS * 4U;
}
