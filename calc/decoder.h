#ifndef NDMIRROR_DECODER_H
#define NDMIRROR_DECODER_H
#include <stdbool.h>
#include <stdint.h>
#include "h264bsd/h264bsd_decoder.h"
typedef struct {
    storage_t *storage;
    bool initialized;
    unsigned offset, stalls;
    uint32_t sequence;
    uint32_t last_result;
    const char *failure;
    uint32_t resume_mb, resume_skip, resume_bit, resume_decoded, resume_pointer;
    int32_t resume_qp;
    uint32_t input_hash;
} MirrorDecoder;
bool mirror_decoder_init(MirrorDecoder *d);
void mirror_decoder_free(MirrorDecoder *d);
/* 0=more work, 1=picture written, -1=invalid stream. Up to 32 macroblocks per call. */
int mirror_decoder_step(MirrorDecoder *d, uint8_t *data, unsigned bytes, uint32_t sequence,
                        uint16_t *pixels, unsigned pitch);
#endif
