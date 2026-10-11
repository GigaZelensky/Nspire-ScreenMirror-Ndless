#include "../../global.h"
#include "../colorspace.h"
#include "../../utils/mem_align.h"
#include "../../../rgb565.h"

#if defined(__GNUC__) || defined(__clang__)
  typedef uint32_t u32_alias __attribute__((__may_alias__));
  typedef uint16_t u16_alias __attribute__((__may_alias__));
#else
  typedef uint32_t u32_alias;
  typedef uint16_t u16_alias;
#endif

/* Five conversion tables plus one shared RGB565 clamp (5,504 bytes). */
static int32_t* g_Ytab;   // 298*(Y-16)
static int32_t* g_UtoB;   // 516*(U-128)
static int32_t* g_UtoG;   // -100*(U-128)
static int32_t* g_VtoR;   // 409*(V-128)
static int32_t* g_VtoG;   // -208*(V-128)

static uint8_t* g_Clamp;

enum { CLAMP_SIZE = 384, RB_BIAS = 64 << 11, G_BIAS = 256 << 10 };

int init_yv12_to_rgb565_tables(void) {
    if (g_Ytab) return 1;
    /* The allocator falls back to RAM when the SRAM pool is full. */
    uint8_t* sramTable = xvid_malloc_sram(5 * 256 * sizeof(int32_t) + CLAMP_SIZE, CACHE_LINE);
    if (!sramTable) return 0;
    g_Ytab = (int32_t*)sramTable;
    g_UtoB = (g_Ytab + 256);
    g_UtoG = (g_UtoB + 256);
    g_VtoR = (g_UtoG + 256);
    g_VtoG = (g_VtoR + 256);
    g_Clamp = (uint8_t*)(g_VtoG + 256);

    for (int i = 0; i < 256; ++i) {
        int y = i - 16;
        g_Ytab[i] = 298 * y + 128; // +128 for rounding before >>8

        int u = i - 128;
        int v = i - 128;

        g_UtoB[i] = 516 * u + RB_BIAS;
        g_UtoG[i] = -100 * u + G_BIAS;
        g_VtoR[i] = 409 * v + RB_BIAS;
        g_VtoG[i] = -208 * v;
    }

    /* Shift directly to the output precision before clipping. Across all
     * 8-bit Y/U/V values, red/blue indices fit [29,130], green [213,364].
     * Keep the negative luma contribution below Y=16, matching Xvid exactly. */
    for (int i = 0; i < CLAMP_SIZE; ++i) {
        int limit = i < 192 ? 31 : 63;
        int value = i - (i < 192 ? 64 : 256);
        if (value < 0) value = 0;
        if (value > limit) value = limit;
        g_Clamp[i] = (uint8_t)value;
    }
    return 1;
}

__attribute__((hot))
static inline uint16_t yuv_to_rgb565_pixel(
    uint8_t y,
    int32_t vr,
    int32_t ugvg,
    int32_t ub,
    const int32_t* Ytab,
    const uint8_t* clip)
{
    /* Arithmetic shifts retain the original signed rounding. */
    int32_t c = Ytab[y];
    unsigned r = clip[(c + vr) >> 11];
    unsigned g = clip[(c + ugvg) >> 10];
    unsigned b = clip[(c + ub) >> 11];
    return (uint16_t)((r << 11) | (g << 5) | b);
}

__attribute__((hot))
void yv12_to_rgb565_concept(
    uint8_t *restrict x_ptr,
    int x_stride,
    uint8_t *restrict y_src,
    uint8_t *restrict u_src,
    uint8_t *restrict v_src,
    int y_stride,
    int uv_stride,
    int width,
    int height,
    int vflip
) {

#if defined(__arm__) && !defined(__thumb__)
    if (width > 0 && height > 0 && !((width | height) & 1) &&
        !((uintptr_t)x_ptr & 3U) && !(x_stride & 3)) {
        int stride = vflip ? -x_stride : x_stride;
        uint8_t *dst = x_ptr + (vflip ? (height - 1) * x_stride : 0);
        for (int row = 0; row < height; row += 2) {
            yuv420_rgb565_pair_rows(y_src, y_src + y_stride, u_src, v_src,
                (uint16_t *)dst, (uint16_t *)(dst + stride), g_Ytab, (unsigned)width / 2U);
            y_src += y_stride * 2;
            u_src += uv_stride;
            v_src += uv_stride;
            dst += stride * 2;
        }
        return;
    }
#endif

    // Local table bases (kept in regs more readily)
    const int32_t* Ytab = g_Ytab;
    const int32_t* VtoR = g_VtoR;
    const int32_t* VtoG = g_VtoG;
    const int32_t* UtoB = g_UtoB;
    const int32_t* UtoG = g_UtoG;

    // The conversion tables already include the positive clamp-index bias.
    const uint8_t* clip = g_Clamp;

    // Output setup (word-based stores)
    int dst_stride_words = x_stride >> 2;
    u32_alias* dst_row = (u32_alias*)x_ptr;

    if (vflip) {
        dst_row = (u32_alias*)(x_ptr + (height - 1) * x_stride);
        dst_stride_words = -dst_stride_words;
    }

    // Input row pointers
    const uint8_t* y_row = y_src;
    const uint8_t* u_row = u_src;
    const uint8_t* v_row = v_src;

    // Process 2 rows at a time (YV12 4:2:0 => one UV row per two Y rows)
    for (int y = 0; y < height; y += 2) {
        const uint8_t* y0 = y_row;
        const uint8_t* y1 = y_row + y_stride;

        // UV row pointers (byte), but we'll read 16-bit chunks in the hot loop
        const u16_alias* u16p = (const u16_alias*)u_row;
        const u16_alias* v16p = (const u16_alias*)v_row;

        // Y row pointers as 32-bit (4 pixels per load)
        const u32_alias* y0_32 = (const u32_alias*)y0;
        const u32_alias* y1_32 = (const u32_alias*)y1;

        // Destination: each u32 store writes 2 RGB565 pixels
        u32_alias* dst0 = dst_row;
        u32_alias* dst1 = dst_row + dst_stride_words;

        // 4 columns per iter (2 UV samples per iter)
        int n4 = width >> 2;     // number of 4-pixel groups
        int rem2 = width & 2;    // remaining 2 pixels? (0 or 2)

        for (int i = 0; i < n4; ++i) {
            // Two chroma samples packed into one halfword each
            uint16_t u01 = *u16p++;
            uint16_t v01 = *v16p++;

            // Four Ys per row
            uint32_t y0_4 = *y0_32++;
            uint32_t y1_4 = *y1_32++;

            // ---- Sample 0 (columns x..x+1) ----
            uint8_t u0 = (uint8_t)(u01);
            uint8_t v0 = (uint8_t)(v01);

            int32_t vr0   = VtoR[v0];
            int32_t ub0   = UtoB[u0];
            int32_t ugvg0 = UtoG[u0] + VtoG[v0];

            uint8_t y00 = (uint8_t)(y0_4);
            uint8_t y01 = (uint8_t)(y0_4 >> 8);
            uint8_t y10 = (uint8_t)(y1_4);
            uint8_t y11 = (uint8_t)(y1_4 >> 8);

            uint16_t p00 = yuv_to_rgb565_pixel(y00, vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p01 = yuv_to_rgb565_pixel(y01, vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p10 = yuv_to_rgb565_pixel(y10, vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p11 = yuv_to_rgb565_pixel(y11, vr0, ugvg0, ub0, Ytab, clip);

            // ---- Sample 1 (columns x+2..x+3) ----
            uint8_t u1 = (uint8_t)(u01 >> 8);
            uint8_t v1 = (uint8_t)(v01 >> 8);

            int32_t vr1   = VtoR[v1];
            int32_t ub1   = UtoB[u1];
            int32_t ugvg1 = UtoG[u1] + VtoG[v1];

            uint8_t y02 = (uint8_t)(y0_4 >> 16);
            uint8_t y03 = (uint8_t)(y0_4 >> 24);
            uint8_t y12 = (uint8_t)(y1_4 >> 16);
            uint8_t y13 = (uint8_t)(y1_4 >> 24);

            uint16_t p02 = yuv_to_rgb565_pixel(y02, vr1, ugvg1, ub1, Ytab, clip);
            uint16_t p03 = yuv_to_rgb565_pixel(y03, vr1, ugvg1, ub1, Ytab, clip);
            uint16_t p12 = yuv_to_rgb565_pixel(y12, vr1, ugvg1, ub1, Ytab, clip);
            uint16_t p13 = yuv_to_rgb565_pixel(y13, vr1, ugvg1, ub1, Ytab, clip);

            // Store: 2 pixels per word
            dst0[0] = (uint32_t)p00 | ((uint32_t)p01 << 16);
            dst0[1] = (uint32_t)p02 | ((uint32_t)p03 << 16);
            dst1[0] = (uint32_t)p10 | ((uint32_t)p11 << 16);
            dst1[1] = (uint32_t)p12 | ((uint32_t)p13 << 16);

            dst0 += 2;
            dst1 += 2;
        }

        if (rem2) {
            const uint8_t* y0_tail = (const uint8_t*) y0_32;
            const uint8_t* y1_tail = (const uint8_t*) y1_32;
            const uint8_t* u_tail = (const uint8_t*) u16p;
            const uint8_t* v_tail = (const uint8_t*) v16p;
            uint8_t u0 = u_tail[0];
            uint8_t v0 = v_tail[0];
            int32_t vr0 = VtoR[v0];
            int32_t ub0 = UtoB[u0];
            int32_t ugvg0 = UtoG[u0] + VtoG[v0];
            uint16_t p00 = yuv_to_rgb565_pixel(y0_tail[0], vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p01 = yuv_to_rgb565_pixel(y0_tail[1], vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p10 = yuv_to_rgb565_pixel(y1_tail[0], vr0, ugvg0, ub0, Ytab, clip);
            uint16_t p11 = yuv_to_rgb565_pixel(y1_tail[1], vr0, ugvg0, ub0, Ytab, clip);

            dst0[0] = (uint32_t) p00 | ((uint32_t) p01 << 16);
            dst1[0] = (uint32_t) p10 | ((uint32_t) p11 << 16);
        }

        // Advance to next 2-row block
        y_row  += 2 * y_stride;
        u_row  += uv_stride;
        v_row  += uv_stride;
        dst_row += 2 * dst_stride_words;
    }
}
