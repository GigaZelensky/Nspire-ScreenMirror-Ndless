#include "pixels.h"
static uint16_t base[320 * 240];
static uint8_t scratch[MIRROR_FRAME];
static bool valid;
void mirror_pixels_reset(void) { valid = false; }
bool mirror_lz4_decode(const uint8_t *src, unsigned size, uint8_t *out, unsigned capacity,
                       unsigned *written) {
    unsigned at = 0, to = 0;
    while (at < size) {
        unsigned token = src[at++], literals = token >> 4;
        if (literals > capacity)
            return false;
        if (literals == 15) {
            unsigned add;
            do {
                if (at >= size)
                    return false;
                add = src[at++];
                if (add > capacity - literals)
                    return false;
                literals += add;
            } while (add == 255);
        }
        if (literals > size - at || literals > capacity - to)
            return false;
        memcpy(out + to, src + at, literals);
        at += literals;
        to += literals;
        if (at == size) {
            *written = to;
            return true;
        }
        if (size - at < 2)
            return false;
        unsigned distance = src[at] | (unsigned)src[at + 1] << 8;
        at += 2;
        if (!distance || distance > to)
            return false;
        unsigned length = (token & 15) + 4;
        if (length > capacity)
            return false;
        if ((token & 15) == 15) {
            unsigned add;
            do {
                if (at >= size)
                    return false;
                add = src[at++];
                if (add > capacity - length)
                    return false;
                length += add;
            } while (add == 255);
        }
        if (length > capacity - to)
            return false;
        if (distance >= length) {
            memcpy(out + to, out + to - distance, length);
            to += length;
        } else {
            unsigned copied = 0;
            while (copied < length) {
                unsigned n = distance + copied;
                if (n > length - copied)
                    n = length - copied;
                memcpy(out + to + copied, out + to - distance, n);
                copied += n;
            }
            to += length;
        }
    }
    return false;
}
static unsigned half(const uint8_t *p) { return p[0] | (unsigned)p[1] << 8; }
bool mirror_pixels_decode(const MirrorFrame *frame, uint16_t *out, unsigned pitch) {
    if (!out || pitch < 320)
        return false;
    const uint8_t *data = frame->data;
    unsigned bytes = frame->total;
    if (frame->flags & MIRROR_LZ4) {
        if (bytes < 5)
            return false;
        unsigned expected = wire_get(data), written = 0;
        if (!expected || expected > sizeof(scratch) ||
            !mirror_lz4_decode(data + 4, bytes - 4, scratch, expected, &written) ||
            written != expected)
            return false;
        data = scratch;
        bytes = expected;
    }
    if (frame->flags & MIRROR_RAW) {
        if (bytes != sizeof(base))
            return false;
        memcpy(base, data, sizeof(base));
        valid = true;
    } else if (frame->flags & MIRROR_DELTA) {
        if (!valid || bytes < 4 || half(data) > 300 || half(data + 2))
            return false;
        unsigned count = half(data), at = 4;
        /* Validate the complete rectangle list before modifying its reference. */
        for (unsigned i = 0; i < count; ++i) {
            if (bytes - at < 8)
                return false;
            unsigned x = half(data + at), y = half(data + at + 2), w = half(data + at + 4),
                     h = half(data + at + 6);
            at += 8;
            if (x >= 320 || y >= 240 || !w || !h || w > 320 - x || h > 240 - y ||
                w * h * 2U > bytes - at)
                return false;
            at += w * h * 2U;
        }
        if (at != bytes)
            return false;
        at = 4;
        for (unsigned i = 0; i < count; ++i) {
            unsigned x = half(data + at), y = half(data + at + 2), w = half(data + at + 4),
                     h = half(data + at + 6);
            at += 8;
            for (unsigned row = 0; row < h; ++row) {
                memcpy(base + (y + row) * 320 + x, data + at, w * 2U);
                at += w * 2U;
            }
        }
    } else
        return false;
    for (unsigned row = 0; row < 240; ++row)
        memcpy(out + row * pitch, base + row * 320, 640);
    return true;
}
