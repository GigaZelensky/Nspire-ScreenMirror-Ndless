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
     2. External compiler flags
     3. Module defines
     4. Local function prototypes
     5. Functions
          h264bsdWriteMacroblock
          h264bsdWriteOutputBlocks

------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------
    1. Include headers
------------------------------------------------------------------------------*/

#include "h264bsd_image.h"
#include "h264bsd_util.h"
#include "h264bsd_neighbour.h"

/*------------------------------------------------------------------------------
    2. External compiler flags
--------------------------------------------------------------------------------

--------------------------------------------------------------------------------
    3. Module defines
------------------------------------------------------------------------------*/

/* x- and y-coordinates for each block, defined in h264bsd_intra_prediction.c */
extern const u32 h264bsdBlockX[];
extern const u32 h264bsdBlockY[];

/* clipping table, defined in h264bsd_intra_prediction.c */
extern const u8 *h264bsdClip;

/*------------------------------------------------------------------------------
    4. Local function prototypes
------------------------------------------------------------------------------*/



/*------------------------------------------------------------------------------

    Function: h264bsdWriteMacroblock

        Functional description:
            Write one macroblock into the image. Both luma and chroma
            components will be written at the same time.

        Inputs:
            data    pointer to macroblock data to be written, 256 values for
                    luma followed by 64 values for both chroma components

        Outputs:
            image   pointer to the image where the macroblock will be written

        Returns:
            none

------------------------------------------------------------------------------*/
#ifndef H264DEC_NEON
void h264bsdWriteMacroblock(image_t *image, u8 *data)
{

/* Variables */

    typedef struct { u32 word[4]; } row16_t;
    typedef struct { u32 word[2]; } row8_t;
    u32 i;
    u32 stride;
    u8 *lum, *cb, *cr;

    ASSERT(image);
    ASSERT(data);
    stride = image->width * 16;
    lum = image->luma;
    cb = image->cb;
    cr = image->cr;
    ASSERT(!((u32)data & 3));
    ASSERT(!((u32)lum & 3));
    ASSERT(!((u32)cb & 3));
    ASSERT(!((u32)cr & 3));

    /* Word-aligned row copies let ARM use load/store-multiple instructions. */
    for (i = 0; i < 16; ++i) {
        *(row16_t *)lum = *(const row16_t *)data;
        lum += stride;
        data += 16;
    }
    stride >>= 1;
    for (i = 0; i < 8; ++i) {
        *(row8_t *)cb = *(const row8_t *)data;
        cb += stride;
        data += 8;
    }
    for (i = 0; i < 8; ++i) {
        *(row8_t *)cr = *(const row8_t *)data;
        cr += stride;
        data += 8;
    }

}
#endif
#ifndef H264DEC_OMXDL
/* DC-only blocks add the same residual to every sample. Bias the existing
 * clipping table once instead of expanding and loading sixteen coefficients. */
void h264bsdAddDcResidual(u8 *dst, u32 dstStride, const u8 *src, u32 srcStride, i32 dc)
{
    const u8 *clip = h264bsdClip + 512 + dc;
    u32 row;
    for (row = 0; row < 4; ++row) {
        dst[0] = clip[src[0]];
        dst[1] = clip[src[1]];
        dst[2] = clip[src[2]];
        dst[3] = clip[src[3]];
        dst += dstStride;
        src += srcStride;
    }
}

/*------------------------------------------------------------------------------

    Function: h264bsdWriteOutputBlocks

        Functional description:
            Write one macroblock into the image. Prediction for the macroblock
            and the residual are given separately and will be combined while
            writing the data to the image

        Inputs:
            data        pointer to macroblock prediction data, 256 values for
                        luma followed by 64 values for both chroma components
            mbNum       number of the macroblock
            residual    pointer to residual data, 16 16-element arrays for luma
                        followed by 4 16-element arrays for both chroma
                        components

        Outputs:
            image       pointer to the image where the data will be written

        Returns:
            none

------------------------------------------------------------------------------*/

void h264bsdWriteOutputBlocks(image_t *image, u32 mbNum, u8 *data,
        i32 residual[][16])
{

/* Variables */

    u32 i;
    u32 picWidth;
    u8 *lum, *cb, *cr;
    u8 *imageBlock;
    u8 *tmp;
    u32 block;
    u32 x, y;
    i32 *pRes;
    i32 tmp1, tmp2, tmp3, tmp4;
    const u8 *clp = h264bsdClip + 512;

/* Code */

    ASSERT(image);
    ASSERT(data);
    ASSERT(mbNum < image->width * image->height);
    ASSERT(!((u32)data&0x3));

    /* DecodeMacroblock already called h264bsdSetCurrImageMbPointers for mbNum.
     * Reuse those pointers as WriteMacroblock does, avoiding a second ARM
     * software division and repeated YUV plane/row address calculations. */
    lum = image->luma;
    cb = image->cb;
    cr = image->cr;
    picWidth = image->width * 16;

    for (block = 0; block < 16; block++)
    {
        x = h264bsdBlockX[block];
        y = h264bsdBlockY[block];

        pRes = residual[block];

        ASSERT(pRes);

        tmp = data + y*16 + x;
        imageBlock = lum + y*picWidth + x;

        ASSERT(!((u32)tmp&0x3));
        ASSERT(!((u32)imageBlock&0x3));

        if (IS_RESIDUAL_DC(pRes)) {
            h264bsdAddDcResidual(imageBlock, picWidth, tmp, 16, pRes[1]);
        }
        else if (IS_RESIDUAL_EMPTY(pRes))
        {
            /*lint -e826 */
            i32 *in32 = (i32*)tmp;
            i32 *out32 = (i32*)imageBlock;

            /* Residual is zero => copy prediction block to output */
            tmp1 = *in32;  in32 += 4;
            tmp2 = *in32;  in32 += 4;
            *out32 = tmp1; out32 += picWidth/4;
            *out32 = tmp2; out32 += picWidth/4;
            tmp1 = *in32;  in32 += 4;
            tmp2 = *in32;
            *out32 = tmp1; out32 += picWidth/4;
            *out32 = tmp2;
        }
        else
        {

            RANGE_CHECK_ARRAY(pRes, -512, 511, 16);

            /* Calculate image = prediction + residual
             * Process four pixels in a loop */
            for (i = 4; i; i--)
            {
                tmp1 = tmp[0];
                tmp2 = *pRes++;
                tmp3 = tmp[1];
                tmp1 = clp[tmp1 + tmp2];
                tmp4 = *pRes++;
                imageBlock[0] = (u8)tmp1;
                tmp3 = clp[tmp3 + tmp4];
                tmp1 = tmp[2];
                tmp2 = *pRes++;
                imageBlock[1] = (u8)tmp3;
                tmp1 = clp[tmp1 + tmp2];
                tmp3 = tmp[3];
                tmp4 = *pRes++;
                imageBlock[2] = (u8)tmp1;
                tmp3 = clp[tmp3 + tmp4];
                tmp += 16;
                imageBlock[3] = (u8)tmp3;
                imageBlock += picWidth;
            }
        }

    }

    picWidth /= 2;

    for (block = 16; block <= 23; block++)
    {
        x = h264bsdBlockX[block & 0x3];
        y = h264bsdBlockY[block & 0x3];

        pRes = residual[block];

        ASSERT(pRes);

        tmp = data + 256;
        imageBlock = cb;

        if (block >= 20)
        {
            imageBlock = cr;
            tmp += 64;
        }

        tmp += y*8 + x;
        imageBlock += y*picWidth + x;

        ASSERT(!((u32)tmp&0x3));
        ASSERT(!((u32)imageBlock&0x3));

        if (IS_RESIDUAL_DC(pRes)) {
            h264bsdAddDcResidual(imageBlock, picWidth, tmp, 8, pRes[1]);
        }
        else if (IS_RESIDUAL_EMPTY(pRes))
        {
            /*lint -e826 */
            i32 *in32 = (i32*)tmp;
            i32 *out32 = (i32*)imageBlock;

            /* Residual is zero => copy prediction block to output */
            tmp1 = *in32;  in32 += 2;
            tmp2 = *in32;  in32 += 2;
            *out32 = tmp1; out32 += picWidth/4;
            *out32 = tmp2; out32 += picWidth/4;
            tmp1 = *in32;  in32 += 2;
            tmp2 = *in32;
            *out32 = tmp1; out32 += picWidth/4;
            *out32 = tmp2;
        }
        else
        {

            RANGE_CHECK_ARRAY(pRes, -512, 511, 16);

            for (i = 4; i; i--)
            {
                tmp1 = tmp[0];
                tmp2 = *pRes++;
                tmp3 = tmp[1];
                tmp1 = clp[tmp1 + tmp2];
                tmp4 = *pRes++;
                imageBlock[0] = (u8)tmp1;
                tmp3 = clp[tmp3 + tmp4];
                tmp1 = tmp[2];
                tmp2 = *pRes++;
                imageBlock[1] = (u8)tmp3;
                tmp1 = clp[tmp1 + tmp2];
                tmp3 = tmp[3];
                tmp4 = *pRes++;
                imageBlock[2] = (u8)tmp1;
                tmp3 = clp[tmp3 + tmp4];
                tmp += 8;
                imageBlock[3] = (u8)tmp3;
                imageBlock += picWidth;
            }
        }
    }

}
#endif /* H264DEC_OMXDL */

