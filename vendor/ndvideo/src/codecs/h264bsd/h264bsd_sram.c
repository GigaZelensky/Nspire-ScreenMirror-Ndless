#include "h264bsd_sram.h"
#include <stddef.h>
#include <stdint.h>

bool h264bsdInitClipTable(void);
bool h264bsdInitQpCTable(void);
bool h264bsdInitDeblockingTables(void);

static bool g_h264_clip_in_sram = false;
static bool g_h264_qpc_in_sram = false;
static bool g_h264_deblocking_in_sram = false;

size_t h264bsdClipTableBytes(void);
size_t h264bsdQpCTableBytes(void);
size_t h264bsdDeblockingTableBytes(void);
bool h264bsdBindClipTable(void *,size_t);
bool h264bsdBindQpCTable(void *,size_t);
bool h264bsdBindDeblockingTables(void *,size_t);
static bool valid_block(void *p,size_t bytes,size_t required)
{ return p ? !((uintptr_t)p&31U) && bytes>=required : bytes==0; }
bool h264bsdBindSramTables(void *clip,size_t clip_bytes,void *qpc,size_t qpc_bytes,
                          void *deblock,size_t deblock_bytes)
{
    if(!valid_block(clip,clip_bytes,h264bsdClipTableBytes()) ||
       !valid_block(qpc,qpc_bytes,h264bsdQpCTableBytes()) ||
       !valid_block(deblock,deblock_bytes,h264bsdDeblockingTableBytes()))return false;
    if(!h264bsdBindClipTable(clip,clip_bytes) || !h264bsdBindQpCTable(qpc,qpc_bytes) ||
       !h264bsdBindDeblockingTables(deblock,deblock_bytes))return false;
    g_h264_clip_in_sram=clip!=NULL;g_h264_qpc_in_sram=qpc!=NULL;g_h264_deblocking_in_sram=deblock!=NULL;
    return true;
}

bool h264bsdInitSramTables(void)
{
    g_h264_clip_in_sram = h264bsdInitClipTable();
    g_h264_qpc_in_sram = h264bsdInitQpCTable();
    g_h264_deblocking_in_sram = h264bsdInitDeblockingTables();
    return g_h264_clip_in_sram &&
        g_h264_qpc_in_sram &&
        g_h264_deblocking_in_sram;
}

void h264bsdGetSramStatus(bool *clip_in_sram, bool *qpc_in_sram, bool *deblocking_in_sram)
{
    if (clip_in_sram) {
        *clip_in_sram = g_h264_clip_in_sram;
    }
    if (qpc_in_sram) {
        *qpc_in_sram = g_h264_qpc_in_sram;
    }
    if (deblocking_in_sram) {
        *deblocking_in_sram = g_h264_deblocking_in_sram;
    }
}
