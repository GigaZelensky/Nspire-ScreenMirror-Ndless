#ifndef MIRROR_PROFILE_H
#define MIRROR_PROFILE_H
#include <stdint.h>
#include <stdbool.h>
enum {
    PROFILE_DECODE,
    PROFILE_BLIT,
    PROFILE_READ,
    PROFILE_WRITE,
    PROFILE_POLL,
    PROFILE_SLEEP,
    PROFILE_INPUT,
    PROFILE_STAGES
};
typedef struct {
    bool enabled, high_resolution;
    const volatile uint32_t *counter;
    uint32_t os_hz, hz, period, control, timer_base, frames, calls, rx_active, rx_cpu, max_used,
        main_task, callback_task, bad_clock;
    uint64_t started, stopped, time[PROFILE_STAGES];
} MirrorProfile;
extern MirrorProfile profile;
void profile_start(const volatile uint32_t *counter, uint32_t hz);
void profile_bind_main(void);
void profile_stop(void);
uint64_t profile_now(void);
void profile_add(unsigned stage, uint64_t start);
unsigned profile_reply(uint8_t *reply);
#endif
