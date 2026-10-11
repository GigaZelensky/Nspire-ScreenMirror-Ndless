/*
 * Copyright (C) 2009 The Android Open Source Project
 * Modified for use by h264bsd standalone library
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*------------------------------------------------------------------------------

    Table of contents

    1. Include headers
    2. Module defines
    3. Data types
    4. Function prototypes

------------------------------------------------------------------------------*/

#ifndef H264SWDEC_IMAGE_H
#define H264SWDEC_IMAGE_H

/*------------------------------------------------------------------------------
    1. Include headers
------------------------------------------------------------------------------*/

#include "basetype.h"

/*------------------------------------------------------------------------------
    2. Module defines
------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------
    3. Data types
------------------------------------------------------------------------------*/

typedef struct
{
    u8 *data;
    u32 width;
    u32 height;
    /* current MB's components */
    u8 *luma;
    u8 *cb;
    u8 *cr;
} image_t;

/* The full calculator width is 20 macroblocks. A constant divisor lets ARM
 * use a multiply/shift instead of a software division; other widths retain
 * the generic calculation. */
static inline u32 h264bsdMbRow(u32 mbNum, u32 width)
{
    return width == 20U ? mbNum / 20U : mbNum / width;
}

/*------------------------------------------------------------------------------
    4. Function prototypes
------------------------------------------------------------------------------*/

void h264bsdAddDcResidual(u8 *dst, u32 dstStride, const u8 *src, u32 srcStride, i32 dc);

void h264bsdWriteMacroblock(image_t *image, u8 *data);

#ifndef H264DEC_OMXDL
void h264bsdWriteOutputBlocks(image_t *image, u32 mbNum, u8 *data,
    i32 residual[][16]);
#endif

#endif /* #ifdef H264SWDEC_IMAGE_H */

