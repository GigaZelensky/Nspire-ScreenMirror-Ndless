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
          h264bsdProcessBlock
          h264bsdProcessLumaDc
          h264bsdProcessChromaDc

------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------
    1. Include headers
------------------------------------------------------------------------------*/

#include "basetype.h"
#include "h264bsd_transform.h"
#include "h264bsd_util.h"

/*------------------------------------------------------------------------------
    2. External compiler flags
--------------------------------------------------------------------------------

--------------------------------------------------------------------------------
    3. Module defines
------------------------------------------------------------------------------*/

/* Switch off the following Lint messages for this file:
 * Info 701: Shift left of signed quantity (int)
 * Info 702: Shift right of signed quantity (int)
 */
/*lint -e701 -e702 */

/* LevelScale function */
static const i32 levelScale[6][3] = {
    {10,13,16}, {11,14,18}, {13,16,20}, {14,18,23}, {16,20,25}, {18,23,29}};

/* levelScale[qp % 6][column] << (qp / 6), precomputed for all QPs.
 * These repeat for every residual block; keep them in read-only storage
 * instead of loading both QP maps and rebuilding three multipliers. */
static const i32 blockScale[52][3] = {
    {10,13,16},
    {11,14,18},
    {13,16,20},
    {14,18,23},
    {16,20,25},
    {18,23,29},
    {20,26,32},
    {22,28,36},
    {26,32,40},
    {28,36,46},
    {32,40,50},
    {36,46,58},
    {40,52,64},
    {44,56,72},
    {52,64,80},
    {56,72,92},
    {64,80,100},
    {72,92,116},
    {80,104,128},
    {88,112,144},
    {104,128,160},
    {112,144,184},
    {128,160,200},
    {144,184,232},
    {160,208,256},
    {176,224,288},
    {208,256,320},
    {224,288,368},
    {256,320,400},
    {288,368,464},
    {320,416,512},
    {352,448,576},
    {416,512,640},
    {448,576,736},
    {512,640,800},
    {576,736,928},
    {640,832,1024},
    {704,896,1152},
    {832,1024,1280},
    {896,1152,1472},
    {1024,1280,1600},
    {1152,1472,1856},
    {1280,1664,2048},
    {1408,1792,2304},
    {1664,2048,2560},
    {1792,2304,2944},
    {2048,2560,3200},
    {2304,2944,3712},
    {2560,3328,4096},
    {2816,3584,4608},
    {3328,4096,5120},
    {3584,4608,5888}
};

/* qp % 6 as a function of qp */
static const u8 qpMod6[52] = {0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5,
    0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3};

/* qp / 6 as a function of qp */
static const u8 qpDiv6[52] = {0,0,0,0,0,0,1,1,1,1,1,1,2,2,2,2,2,2,3,3,3,3,3,3,
    4,4,4,4,4,4,5,5,5,5,5,5,6,6,6,6,6,6,7,7,7,7,7,7,8,8,8,8};

/*------------------------------------------------------------------------------
    4. Local function prototypes
------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------

    Function: h264bsdProcessBlock

        Functional description:
            Function performs inverse zig-zag scan, inverse scaling and
            inverse transform for a luma or a chroma residual block

        Inputs:
            data            pointer to data to be processed
            qp              quantization parameter
            skip            skip processing of data[0], set to non-zero value
                            if dc coeff hanled separately
            coeffMap        16 lsb's indicate which coeffs are non-zero,
                            bit 0 (lsb) for coeff 0, bit 1 for coeff 1 etc.

        Outputs:
            data            processed data

        Returns:
            HANTRO_OK       success
            HANTRO_NOK      processed data not in valid range [-512, 511]

------------------------------------------------------------------------------*/
u32 h264bsdProcessBlock(i32 *data, u32 qp, u32 skip, u32 coeffMap)
{

/* Variables */

    i32 tmp0, tmp1, tmp2, tmp3;
    i32 d1, d2, d3;
    u32 row,col;
    i32 *ptr;

/* Code */

    tmp1 = blockScale[qp][0];
    tmp2 = blockScale[qp][1];
    tmp3 = blockScale[qp][2];

    if (!skip)
        data[0] = (data[0] * tmp1);

    /* at least one of the rows 1, 2 or 3 contain non-zero coeffs, mask takes
     * the scanning order into account */
    if (coeffMap & 0xFF9C)
    {
        /* do the zig-zag scan and inverse quantization */
        d1 = data[1];
        d2 = data[14];
        d3 = data[15];
        data[1] = (d1 * tmp2);
        data[14] = (d2 * tmp2);
        data[15] = (d3 * tmp3);

        d1 = data[2];
        d2 = data[5];
        d3 = data[4];
        data[4] = (d1 * tmp2);
        data[2]  = (d2 * tmp1);
        data[5] = (d3 * tmp3);

        d1 = data[8];
        d2 = data[3];
        d3 = data[6];
        tmp0 = (d1 * tmp2);
        data[8] = (d2 * tmp1);
        data[3]  = (d3 * tmp2);
        d1 = data[7];
        d2 = data[12];
        d3 = data[9];
        data[6]  = (d1 * tmp2);
        data[7]  = (d2 * tmp3);
        data[12] = (d3 * tmp2);
        data[9]  = tmp0;

        d1 = data[10];
        d2 = data[11];
        d3 = data[13];
        data[13] = (d1 * tmp3);
        data[10] = (d2 * tmp1);
        data[11] = (d3 * tmp2);

        /* horizontal transform */
        for (row = 4, ptr = data; row--; ptr += 4)
        {
            tmp0 = ptr[0] + ptr[2];
            tmp1 = ptr[0] - ptr[2];
            tmp2 = (ptr[1] >> 1) - ptr[3];
            tmp3 = ptr[1] + (ptr[3] >> 1);
            ptr[0] = tmp0 + tmp3;
            ptr[1] = tmp1 + tmp2;
            ptr[2] = tmp1 - tmp2;
            ptr[3] = tmp0 - tmp3;
        }

        /*lint +e661 +e662*/
        /* then vertical transform */
        for (col = 4; col--; data++)
        {
            tmp0 = data[0] + data[8];
            tmp1 = data[0] - data[8];
            tmp2 = (data[4] >> 1) - data[12];
            tmp3 = data[4] + (data[12] >> 1);
            data[0 ] = (tmp0 + tmp3 + 32)>>6;
            data[4 ] = (tmp1 + tmp2 + 32)>>6;
            data[8 ] = (tmp1 - tmp2 + 32)>>6;
            data[12] = (tmp0 - tmp3 + 32)>>6;
            /* After adding 512, every value must use only its low ten bits.
             * OR preserves any out-of-range bit across all four samples. */
            if ((((u32)data[0] + 512U) |
                 ((u32)data[4] + 512U) |
                 ((u32)data[8] + 512U) |
                 ((u32)data[12] + 512U)) > 1023U)
                return(HANTRO_NOK);
        }
    }
    else /* rows 1, 2 and 3 are zero */
    {
        /* only dc-coeff is non-zero, i.e. coeffs at original positions
         * 1, 5 and 6 are zero */
        if ((coeffMap & 0x62) == 0)
        {
            tmp0 = (data[0] + 32) >> 6;
            /* check that value is in the range [-512,511] */
            if ((u32)(tmp0 + 512) > 1023)
                return(HANTRO_NOK);
#ifdef H264DEC_NEON
            data[0] = data[1] = data[2] = data[3] = data[4] = data[5] =
                data[6] = data[7] = data[8] = data[9] = data[10] = data[11] =
                data[12] = data[13] = data[14] = data[15] = tmp0;
#else
            /* A DC-only inverse transform produces one constant over 4x4.
             * Retain that compact form until prediction is added. */
            if (!tmp0)
                MARK_RESIDUAL_EMPTY(data);
            else
                MARK_RESIDUAL_DC(data, tmp0);
#endif
        }
        else /* at least one of the coeffs 1, 5 or 6 is non-zero */
        {
            data[1] = (data[1] * tmp2);
            data[2] = (data[5] * tmp1);
            data[3] = (data[6] * tmp2);
            tmp0 = data[0] + data[2];
            tmp1 = data[0] - data[2];
            tmp2 = (data[1] >> 1) - data[3];
            tmp3 = data[1] + (data[3] >> 1);
            data[0] = (tmp0 + tmp3 + 32)>>6;
            data[1] = (tmp1 + tmp2 + 32)>>6;
            data[2] = (tmp1 - tmp2 + 32)>>6;
            data[3] = (tmp0 - tmp3 + 32)>>6;
            data[4] = data[8] = data[12] = data[0];
            data[5] = data[9] = data[13] = data[1];
            data[6] = data[10] = data[14] = data[2];
            data[7] = data[11] = data[15] = data[3];
            /* After adding 512, every value must use only its low ten bits.
             * OR preserves any out-of-range bit across all four samples. */
            if ((((u32)data[0] + 512U) |
                 ((u32)data[1] + 512U) |
                 ((u32)data[2] + 512U) |
                 ((u32)data[3] + 512U)) > 1023U)
                return(HANTRO_NOK);
        }
    }

    return(HANTRO_OK);

}

/*------------------------------------------------------------------------------

    Function: h264bsdProcessLumaDc

        Functional description:
            Function performs inverse zig-zag scan, inverse transform and
            inverse scaling for a luma DC coefficients block

        Inputs:
            data            pointer to data to be processed
            qp              quantization parameter

        Outputs:
            data            processed data

        Returns:
            none

------------------------------------------------------------------------------*/
void h264bsdProcessLumaDc(i32 *data, u32 qp)
{

/* Variables */

    i32 tmp0, tmp1, tmp2, tmp3;
    u32 row,col;
    u32 qpMod, qpDiv;
    i32 levScale;
    i32 *ptr;

/* Code */

    qpMod = qpMod6[qp];
    qpDiv = qpDiv6[qp];

    /* zig-zag scan */
    tmp0 = data[2];
    data[2]  = data[5];
    data[5] = data[4];
    data[4] = tmp0;

    tmp0 = data[8];
    data[8] = data[3];
    data[3]  = data[6];
    data[6]  = data[7];
    data[7]  = data[12];
    data[12] = data[9];
    data[9]  = tmp0;

    tmp0 = data[10];
    data[10] = data[11];
    data[11] = data[13];
    data[13] = tmp0;

    /* horizontal transform */
    for (row = 4, ptr = data; row--; ptr += 4)
    {
        tmp0 = ptr[0] + ptr[2];
        tmp1 = ptr[0] - ptr[2];
        tmp2 = ptr[1] - ptr[3];
        tmp3 = ptr[1] + ptr[3];
        ptr[0] = tmp0 + tmp3;
        ptr[1] = tmp1 + tmp2;
        ptr[2] = tmp1 - tmp2;
        ptr[3] = tmp0 - tmp3;
    }

    /*lint +e661 +e662*/
    /* then vertical transform and inverse scaling */
    levScale = levelScale[ qpMod ][0];
    if (qp >= 12)
    {
        levScale <<= (qpDiv-2);
        for (col = 4; col--; data++)
        {
            tmp0 = data[0] + data[8 ];
            tmp1 = data[0] - data[8 ];
            tmp2 = data[4] - data[12];
            tmp3 = data[4] + data[12];
            data[0 ] = ((tmp0 + tmp3)*levScale);
            data[4 ] = ((tmp1 + tmp2)*levScale);
            data[8 ] = ((tmp1 - tmp2)*levScale);
            data[12] = ((tmp0 - tmp3)*levScale);
        }
    }
    else
    {
        i32 tmp;
        tmp = ((1 - qpDiv) == 0) ? 1 : 2;
        for (col = 4; col--; data++)
        {
            tmp0 = data[0] + data[8 ];
            tmp1 = data[0] - data[8 ];
            tmp2 = data[4] - data[12];
            tmp3 = data[4] + data[12];
            data[0 ] = ((tmp0 + tmp3)*levScale+tmp) >> (2-qpDiv);
            data[4 ] = ((tmp1 + tmp2)*levScale+tmp) >> (2-qpDiv);
            data[8 ] = ((tmp1 - tmp2)*levScale+tmp) >> (2-qpDiv);
            data[12] = ((tmp0 - tmp3)*levScale+tmp) >> (2-qpDiv);
        }
    }

}

/*------------------------------------------------------------------------------

    Function: h264bsdProcessChromaDc

        Functional description:
            Function performs inverse transform and inverse scaling for a
            chroma DC coefficients block

        Inputs:
            data            pointer to data to be processed
            qp              quantization parameter

        Outputs:
            data            processed data

        Returns:
            none

------------------------------------------------------------------------------*/
void h264bsdProcessChromaDc(i32 *data, u32 qp)
{

/* Variables */

    i32 tmp0, tmp1, tmp2, tmp3;
    u32 qpDiv;
    i32 levScale;
    u32 levShift;

/* Code */

    qpDiv = qpDiv6[qp];
    levScale = levelScale[ qpMod6[qp] ][0];

    if (qp >= 6)
    {
        levScale <<= (qpDiv-1);
        levShift = 0;
    }
    else
    {
        levShift = 1;
    }

    tmp0 = data[0] + data[2];
    tmp1 = data[0] - data[2];
    tmp2 = data[1] - data[3];
    tmp3 = data[1] + data[3];
    data[0] = ((tmp0 + tmp3) * levScale) >> levShift;
    data[1] = ((tmp0 - tmp3) * levScale) >> levShift;
    data[2] = ((tmp1 + tmp2) * levScale) >> levShift;
    data[3] = ((tmp1 - tmp2) * levScale) >> levShift;
    tmp0 = data[4] + data[6];
    tmp1 = data[4] - data[6];
    tmp2 = data[5] - data[7];
    tmp3 = data[5] + data[7];
    data[4] = ((tmp0 + tmp3) * levScale) >> levShift;
    data[5] = ((tmp0 - tmp3) * levScale) >> levShift;
    data[6] = ((tmp1 + tmp2) * levScale) >> levShift;
    data[7] = ((tmp1 - tmp2) * levScale) >> levShift;

}

/*lint +e701 +e702 */


