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
          h264bsdDecodeSliceData
          SetMbParams
          h264bsdMarkSliceCorrupted

------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------
    1. Include headers
------------------------------------------------------------------------------*/

#include "h264bsd_slice_data.h"
#include "h264bsd_util.h"
#include "h264bsd_vlc.h"

/*------------------------------------------------------------------------------
    2. External compiler flags
--------------------------------------------------------------------------------

--------------------------------------------------------------------------------
    3. Module defines
------------------------------------------------------------------------------*/

/*------------------------------------------------------------------------------
    4. Local function prototypes
------------------------------------------------------------------------------*/

static void SetMbParams(mbStorage_t *pMb, sliceHeader_t *pSlice, u32 sliceId,
    i32 chromaQpIndexOffset);

/*------------------------------------------------------------------------------

   5.1  Function name: h264bsdDecodeSliceData

        Functional description:
            Decode one slice. Function decodes stream data, i.e. macroblocks
            and possible skip_run fields. h264bsdDecodeMacroblock function is
            called to handle all other macroblock related processing.
            Macroblock to slice group mapping is considered when next
            macroblock to process is determined (h264bsdNextMbAddress function)
            map

        Inputs:
            pStrmData       pointer to stream data structure
            pStorage        pointer to storage structure
            currImage       pointer to current processed picture, needed for
                            intra prediction of the macroblocks
            pSliceHeader    pointer to slice header of the current slice

        Outputs:
            currImage       processed macroblocks are written to current image
            pStorage        mbStorage structure of each processed macroblock
                            is updated here

        Returns:
            HANTRO_OK       success
            HANTRO_NOK      invalid stream data

------------------------------------------------------------------------------*/

u32 h264bsdDecodeSliceData(strmData_t *pStrmData, storage_t *pStorage,
    image_t *currImage, sliceHeader_t *pSliceHeader)
{

/* Variables */

    u8 mbData[384 + 15 + 32];
    u8 *data;
    u32 tmp;
    u32 skipRun;
    u32 prevSkipped;
    u32 currMbAddr;
    u32 moreMbs;
    u32 nextMbAddr;
    u32 budget_active;
    u32 single_slice_group;
    u32 is_intra_slice;
    i32 qpY;
    macroblockLayer_t *mbLayer;

/* Code */

    ASSERT(pStrmData);
    ASSERT(pSliceHeader);
    ASSERT(pStorage);
    ASSERT(pSliceHeader->firstMbInSlice < pStorage->picSizeInMbs);

    /* ensure 16-byte alignment */
    data = (u8*)ALIGN(mbData, 16);

    mbLayer = pStorage->mbLayer;
    budget_active = pStorage->macroblockBudget != 0U ? HANTRO_TRUE : HANTRO_FALSE;
    single_slice_group = pStorage->activePps->numSliceGroups == 1U;
    is_intra_slice = IS_I_SLICE(pSliceHeader->sliceType);

    if (!pStorage->slice->decodeInProgress)
    {
        currMbAddr = pSliceHeader->firstMbInSlice;
        skipRun = 0;
        prevSkipped = HANTRO_FALSE;

        /* increment slice index, will be one for decoding of the first slice of
         * the picture */
        pStorage->slice->sliceId++;

        /* lastMbAddr stores address of the macroblock that was last successfully
         * decoded, needed for error handling */
        pStorage->slice->lastMbAddr = 0;

        /* initial quantization parameter for the slice is obtained as the sum of
         * initial QP for the picture and sliceQpDelta for the current slice */
        qpY = (i32)pStorage->activePps->picInitQp + pSliceHeader->sliceQpDelta;
        pStorage->slice->decodeInProgress = HANTRO_TRUE;
    }
    else
    {
        currMbAddr = pStorage->slice->currMbAddr;
        skipRun = pStorage->slice->skipRun;
        prevSkipped = pStorage->slice->prevSkipped;
        qpY = pStorage->slice->qpY;
    }
    do
    {
        /* primary picture and already decoded macroblock -> error */
        if (!pSliceHeader->redundantPicCnt && pStorage->mb[currMbAddr].decoded)
        {
            EPRINT("Primary and already decoded");
            return(HANTRO_NOK);
        }

        SetMbParams(pStorage->mb + currMbAddr, pSliceHeader,
            pStorage->slice->sliceId, pStorage->activePps->chromaQpIndexOffset);

        if (!is_intra_slice)
        {
            if (!prevSkipped)
            {
                tmp = h264bsdDecodeExpGolombUnsigned(pStrmData, &skipRun);
                if (tmp != HANTRO_OK)
                    return(tmp);
                /* skip_run shall be less than or equal to number of
                 * macroblocks left */
                if (skipRun > (pStorage->picSizeInMbs - currMbAddr))
                {
                    EPRINT("skip_run");
                    return(HANTRO_NOK);
                }
                if (skipRun)
                {
                    prevSkipped = HANTRO_TRUE;
                    /* Skip prediction consumes only reference 0 and MVD 0;
                     * all other prediction fields are parsed before use. */
                    mbLayer->mbPred.refIdxL0[0] = 0;
                    mbLayer->mbPred.mvdL0[0].hor = 0;
                    mbLayer->mbPred.mvdL0[0].ver = 0;
                    /* mark current macroblock skipped */
                    mbLayer->mbType = P_Skip;
                }
            }
        }

        if (skipRun)
        {
            DEBUG(("Skipping macroblock %d\n", currMbAddr));
            skipRun--;
        }
        else
        {
            prevSkipped = HANTRO_FALSE;
            tmp = h264bsdDecodeMacroblockLayer(pStrmData, mbLayer,
                pStorage->mb + currMbAddr, pSliceHeader->sliceType,
                pSliceHeader->numRefIdxL0Active);
            if (tmp != HANTRO_OK)
            {
                EPRINT("macroblock_layer");
                return(tmp);
            }
        }

        tmp = h264bsdDecodeMacroblock(pStorage->mb + currMbAddr, mbLayer,
            currImage, pStorage->dpb, &qpY, currMbAddr,
            pStorage->activePps->constrainedIntraPredFlag, data);
        if (tmp != HANTRO_OK)
        {
            EPRINT("MACRO_BLOCK");
            return(tmp);
        }

        /* count macroblocks only when they were decoded for the first time
         * (redundant slices do not increase the total) */
        if (pStorage->mb[currMbAddr].decoded == 1)
        {
            if (pStorage->slice->numDecodedMbs >= pStorage->picSizeInMbs)
            {
                EPRINT("Num decoded mbs");
                return(HANTRO_NOK);
            }
            pStorage->slice->numDecodedMbs++;
        }

        /* keep on processing as long as there is stream data left or
         * processing of macroblocks to be skipped based on the last skipRun is
         * not finished */
        /* A remaining skip run already proves that another MB follows. */
        moreMbs = (skipRun || h264bsdMoreRbspData(pStrmData)) ?
                                        HANTRO_TRUE : HANTRO_FALSE;

        /* lastMbAddr is only updated for intra slices (all macroblocks of
         * inter slices will be lost in case of an error) */
        if (is_intra_slice)
            pStorage->slice->lastMbAddr = currMbAddr;

        if (budget_active)
        {
            pStorage->macroblockBudget--;
        }

        nextMbAddr = currMbAddr;
        if (moreMbs)
        {
            if (single_slice_group) {
                nextMbAddr = currMbAddr + 1U;
                if (nextMbAddr >= pStorage->picSizeInMbs)
                    nextMbAddr = 0;
            } else {
                nextMbAddr = h264bsdNextMbAddress(pStorage->sliceGroupMap,
                    pStorage->picSizeInMbs, currMbAddr);
            }
            /* data left in the buffer but no more macroblocks for current slice
             * group -> error */
            if (!nextMbAddr)
            {
                EPRINT("Next mb address");
                return(HANTRO_NOK);
            }
            if (budget_active && pStorage->macroblockBudget == 0)
            {
                pStorage->slice->currMbAddr = nextMbAddr;
                pStorage->slice->skipRun = skipRun;
                pStorage->slice->prevSkipped = prevSkipped;
                pStorage->slice->qpY = qpY;
                return(HANTRO_PENDING);
            }
        }
        currMbAddr = nextMbAddr;

    } while (moreMbs);
    pStorage->slice->decodeInProgress = HANTRO_FALSE;

    return(HANTRO_OK);

}

/*------------------------------------------------------------------------------

   5.2  Function: SetMbParams

        Functional description:
            Set macroblock parameters that remain constant for this slice

        Inputs:
            pSlice      pointer to current slice header
            sliceId     id of the current slice
            chromaQpIndexOffset

        Outputs:
            pMb         pointer to macroblock structure which is updated

        Returns:
            none

------------------------------------------------------------------------------*/

void SetMbParams(mbStorage_t *pMb, sliceHeader_t *pSlice, u32 sliceId,
    i32 chromaQpIndexOffset)
{

/* Variables */
    u32 tmp1;
    i32 tmp2, tmp3;

/* Code */

    tmp1 = pSlice->disableDeblockingFilterIdc;
    tmp2 = pSlice->sliceAlphaC0Offset;
    tmp3 = pSlice->sliceBetaOffset;
    pMb->sliceId = sliceId;
    pMb->disableDeblockingFilterIdc = tmp1;
    pMb->filterOffsetA = tmp2;
    pMb->filterOffsetB = tmp3;
    pMb->chromaQpIndexOffset = chromaQpIndexOffset;

}

/*------------------------------------------------------------------------------

   5.3  Function name: h264bsdMarkSliceCorrupted

        Functional description:
            Mark macroblocks of the slice corrupted. If lastMbAddr in the slice
            storage is set -> picWidhtInMbs (or at least 10) macroblocks back
            from  the lastMbAddr are marked corrupted. However, if lastMbAddr
            is not set -> all macroblocks of the slice are marked.

        Inputs:
            pStorage        pointer to storage structure
            firstMbInSlice  address of the first macroblock in the slice, this
                            identifies the slice to be marked corrupted

        Outputs:
            pStorage        mbStorage for the corrupted macroblocks updated

        Returns:
            none

------------------------------------------------------------------------------*/

void h264bsdMarkSliceCorrupted(storage_t *pStorage, u32 firstMbInSlice)
{

/* Variables */

    u32 tmp, i;
    u32 sliceId;
    u32 currMbAddr;

/* Code */

    ASSERT(pStorage);
    ASSERT(firstMbInSlice < pStorage->picSizeInMbs);

    currMbAddr = firstMbInSlice;

    sliceId = pStorage->slice->sliceId;

    /* DecodeSliceData sets lastMbAddr for I slices -> if it was set, go back
     * MAX(picWidthInMbs, 10) macroblocks and start marking from there */
    if (pStorage->slice->lastMbAddr)
    {
        ASSERT(pStorage->mb[pStorage->slice->lastMbAddr].sliceId == sliceId);
        i = pStorage->slice->lastMbAddr - 1;
        tmp = 0;
        while (i > currMbAddr)
        {
            if (pStorage->mb[i].sliceId == sliceId)
            {
                tmp++;
                if (tmp >= MAX(pStorage->activeSps->picWidthInMbs, 10))
                    break;
            }
            i--;
        }
        currMbAddr = i;
    }

    do
    {

        if ( (pStorage->mb[currMbAddr].sliceId == sliceId) &&
             (pStorage->mb[currMbAddr].decoded) )
        {
            pStorage->mb[currMbAddr].decoded--;
        }
        else
        {
            break;
        }

        currMbAddr = h264bsdNextMbAddress(pStorage->sliceGroupMap,
            pStorage->picSizeInMbs, currMbAddr);

    } while (currMbAddr);

}

