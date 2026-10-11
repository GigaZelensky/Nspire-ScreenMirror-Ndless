#ifndef H264SWDEC_SRAM_H
#define H264SWDEC_SRAM_H

#include <stdbool.h>

bool h264bsdInitSramTables(void);
void h264bsdGetSramStatus(bool *clip_in_sram, bool *qpc_in_sram, bool *deblocking_in_sram);

#endif
