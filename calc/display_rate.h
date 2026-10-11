#ifndef MIRROR_DISPLAY_RATE_H
#define MIRROR_DISPLAY_RATE_H
#include <stdbool.h>
#include <stdint.h>
/* The sender measures displayed frame IDs against wall time. OS ticks can miss
 * elapsed time while this receiver decodes with IRQs masked. */
typedef struct {
    uint32_t tenths;
    bool valid;
} MirrorDisplayRate;
static inline void mirror_display_rate_reset(volatile MirrorDisplayRate *rate) {
    rate->tenths = 0;
    rate->valid = false;
}
static inline bool mirror_display_rate_update(volatile MirrorDisplayRate *rate, unsigned tenths) {
    if (tenths > 1000U)
        return false;
    rate->tenths = tenths;
    rate->valid = true;
    return true;
}
#endif
