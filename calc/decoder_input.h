#ifndef NDMIRROR_DECODER_INPUT_H
#define NDMIRROR_DECODER_INPUT_H
#include "wire.h"
#include <stdio.h>
typedef struct {
    uint8_t data[MIRROR_H264_FRAME];
    uint32_t session, frame, bytes, hash;
    uint32_t pll, emi74, emi1c;
    uint32_t idle_literal;
} MirrorDecoderInput;
static inline uint32_t mirror_input_hash(const uint8_t *data, unsigned bytes) {
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < bytes; ++i)
        hash = (hash ^ data[i]) * 16777619U;
    return hash;
}
static inline bool mirror_input_capture(MirrorDecoderInput *copy, uint32_t session,
                                        const MirrorFrame *frame) {
    if (!frame->total || frame->total > sizeof(copy->data))
        return false;
    memcpy(copy->data, frame->data, frame->total);
    copy->session = session;
    copy->frame = frame->id;
    copy->bytes = frame->total;
    copy->hash = mirror_input_hash(copy->data, copy->bytes);
    return true;
}
static inline bool mirror_input_save(const MirrorDecoderInput *copy, const char *path) {
    if (!copy->bytes || copy->bytes > sizeof(copy->data))
        return false;
    FILE *file = fopen(path, "wb");
    if (!file)
        return false;
    bool saved = fwrite(copy->data, 1, copy->bytes, file) == copy->bytes;
    if (fclose(file))
        saved = false;
    return saved;
}
#endif
