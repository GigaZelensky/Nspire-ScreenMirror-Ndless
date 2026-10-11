#include "decoder.h"
#include "rgb565.h"
#include "sram_tables.h"
#include <string.h>
static struct {
    int32_t table[5][256];
    uint8_t clip[384];
} colors;
static bool colors_ready;
bool mirror_decoder_init(MirrorDecoder *d) {
    memset(d, 0, sizeof(*d));
    d->storage = h264bsdAlloc();
    if (!d->storage)
        return false;
    if (h264bsdInit(d->storage, 1) != 0) {
        h264bsdFree(d->storage);
        d->storage = NULL;
        return false;
    }
    d->initialized = true;
    if (!colors_ready) {
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
            int limit = i < 192 ? 31 : 63, v = i - (i < 192 ? 64 : 256);
            if (v < 0)
                v = 0;
            if (v > limit)
                v = limit;
            colors.clip[i] = (uint8_t)v;
        }
        colors_ready = true;
    }
    return true;
}
void mirror_decoder_free(MirrorDecoder *d) {
    if (d->storage) {
        if (d->initialized)
            h264bsdShutdown(d->storage);
        h264bsdFree(d->storage);
    }
    memset(d, 0, sizeof(*d));
}
static int failed(MirrorDecoder *d, const char *reason) {
    d->failure = reason;
    return -1;
}
int mirror_decoder_step(MirrorDecoder *d, uint8_t *data, unsigned bytes, uint32_t sequence,
                        uint16_t *pixels, unsigned pitch) {
    if (!d->storage || !d->initialized || !bytes || pitch < 320)
        return failed(d, "invalid decoder state or output stride");
    if (d->sequence != sequence) {
        d->sequence = sequence;
        d->input_hash = 2166136261U;
        for (unsigned index = 0; index < bytes; ++index)
            d->input_hash = (d->input_hash ^ data[index]) * 16777619U;
        d->offset = 0;
        d->stalls = 0;
        d->failure = NULL;
    }
    h264bsdSetMacroblockBudget(d->storage, 32);
    for (unsigned calls = 0; calls < 8 && d->offset < bytes; ++calls) {
        d->resume_mb = UINT32_MAX;
        if (d->storage->slice->decodeInProgress) {
            d->resume_mb = d->storage->slice->currMbAddr;
            d->resume_skip = d->storage->slice->skipRun;
            d->resume_qp = d->storage->slice->qpY;
            d->resume_bit = d->storage->strm[0].strmBuffReadBits;
            d->resume_pointer = d->storage->prevBufPointer == data + d->offset;
            d->resume_decoded = d->resume_mb < d->storage->picSizeInMbs
                                    ? d->storage->mb[d->resume_mb].decoded
                                    : UINT32_MAX;
        }
        u32 read = 0,
            rc = h264bsdDecode(d->storage, data + d->offset, bytes - d->offset, sequence, &read);
        d->last_result = rc;
        if (read > bytes - d->offset)
            return failed(d, "decoder consumed beyond input");
        d->offset += read;
        if (rc == H264BSD_HDRS_RDY) {
            if (h264bsdPicWidth(d->storage) != 20 || h264bsdPicHeight(d->storage) != 15 ||
                h264bsdProfile(d->storage) != 66)
                return failed(d, "unsupported dimensions or profile");
        } else if (rc == H264BSD_PIC_RDY) {
            u32 id, idr, errors;
            uint8_t *picture = h264bsdNextOutputPicture(d->storage, &id, &idr, &errors);
            if (!picture || errors || h264bsdPicWidth(d->storage) != 20 ||
                h264bsdPicHeight(d->storage) != 15)
                return failed(d, "missing picture, concealed macroblocks or dimensions");
            const uint8_t *u = picture + 320 * 240, *v = u + 160 * 120;
#if defined(__arm__)
            const int32_t *tables = colors.table[0];
#endif
#if MIRROR_SRAM_TABLES && defined(__arm__)
            tables = mirror_tables_begin(tables);
#endif
            for (unsigned y = 0; y < 240; y += 2) {
#if defined(__arm__)
#if MIRROR_SRAM_TABLES
                if (!(y & 7U))
                    mirror_tables_sample_begin();
#endif
                yuv420_rgb565_pair_rows(picture + y * 320, picture + (y + 1) * 320,
                                        u + (y / 2) * 160, v + (y / 2) * 160, pixels + y * pitch,
                                        pixels + (y + 1) * pitch, tables, 160);
#if MIRROR_SRAM_TABLES
                if ((y & 7U) == 6U)
                    mirror_tables_sample_end();
#endif
#else
                for (unsigned row = y; row < y + 2; ++row)
                    for (unsigned x = 0; x < 320; ++x) {
                        int yy = colors.table[0][picture[row * 320 + x]],
                            uu = u[(row / 2) * 160 + x / 2], vv = v[(row / 2) * 160 + x / 2];
                        pixels[row * pitch + x] =
                            (uint16_t)((colors.clip[(yy + colors.table[3][vv]) >> 11] << 11) |
                                       (colors.clip
                                            [(yy + colors.table[2][uu] + colors.table[4][vv]) >> 10]
                                        << 5) |
                                       colors.clip[(yy + colors.table[1][uu]) >> 11]);
                    }
#endif
            }
#if MIRROR_SRAM_TABLES && defined(__arm__)
            if (!mirror_tables_end())
                return failed(d, "SRAM table restoration verification failed");
#endif
            return 1;
        } else if (rc == H264BSD_PENDING)
            return 0;
        else if (rc != H264BSD_RDY)
            return failed(d, "H264 decode returned error");
        if (!read && rc != H264BSD_HDRS_RDY) {
            if (++d->stalls > 8)
                return failed(d, "decoder made no progress");
        } else
            d->stalls = 0;
    }
    return d->offset < bytes ? 0 : failed(d, "input exhausted without a picture");
}
