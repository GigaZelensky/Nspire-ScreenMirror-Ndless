#ifndef NDMIRROR_DISPLAY_TARGET_H
#define NDMIRROR_DISPLAY_TARGET_H
#include <stdbool.h>
#include <stdint.h>
/* Only the measured identity, uncached RGB565 aperture is suitable. Overlay
 * composition and stateful lossless updates continue using the RAM surface. */
static inline bool mirror_direct_vram_eligible(bool cx2, bool landscape, uint32_t framebuffer,
                                               uint32_t mapping, unsigned pitch, bool overlay,
                                               bool h264) {
    return cx2 && landscape && framebuffer == 0xa8000000U &&
           (mapping & 0xfff00003U) == 0xa8000002U && !(mapping & 4U) && pitch == 640U && !overlay &&
           h264;
}
#endif
