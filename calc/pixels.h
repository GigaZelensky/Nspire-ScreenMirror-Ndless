#ifndef NDMIRROR_PIXELS_H
#define NDMIRROR_PIXELS_H
#include "wire.h"
bool mirror_pixels_decode(const MirrorFrame *frame, uint16_t *out, unsigned pitch);
void mirror_pixels_reset(void);
bool mirror_lz4_decode(const uint8_t *source, unsigned size, uint8_t *out, unsigned capacity,
                       unsigned *written);
#endif
