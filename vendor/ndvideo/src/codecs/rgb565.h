#ifndef NDVIDEO_RGB565_H
#define NDVIDEO_RGB565_H

#include <stdint.h>

/* ARM926 conversion of two YUV420 rows. Width is a nonzero count of pixel
 * pairs; both destinations must be word-aligned. The table layout is Y, U->B,
 * U->G, V->R, V->G (256 int32_t entries each), followed by the biased RGB565
 * clip table. Coefficients and luma-range handling belong to the caller. */
void yuv420_rgb565_pair_rows(const uint8_t *y0, const uint8_t *y1,
    const uint8_t *u, const uint8_t *v, uint16_t *d0, uint16_t *d1,
    const int32_t *y_table, unsigned pairs);

/* Same contract; reuse the conversion for exact flat/repeated 2x2 blocks. */
void yuv420_rgb565_flat_pair_rows(const uint8_t *y0, const uint8_t *y1,
    const uint8_t *u, const uint8_t *v, uint16_t *d0, uint16_t *d1,
    const int32_t *y_table, unsigned pairs);

#endif
