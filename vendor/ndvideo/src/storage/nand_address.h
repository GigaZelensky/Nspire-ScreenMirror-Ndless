#ifndef NDVIDEO_NAND_ADDRESS_H
#define NDVIDEO_NAND_ADDRESS_H
#include <stdbool.h>
#include <stdint.h>
/* Immutable, validated lower-disk view. The caller copies it while holding
 * storage ownership and invalidates it after native writes/relocation.
 * remaps contains count little-endian (source block, replacement block) pairs. */
typedef struct {
    uint32_t media_bytes, device_bytes, before_remap_bytes, physical_base_bytes;
    uint32_t remap_limit_bytes, remap_count;
    const uint8_t *remaps;
    bool remap_enabled;
} NandAddressMap;
typedef enum { NAND_ADDRESS_MORE, NAND_ADDRESS_READY, NAND_ADDRESS_ERROR } NandAddressStatus;
typedef struct {
    NandAddressMap map;
    uint32_t offset, index, source_block, raw_page;
    NandAddressStatus status;
} NandAddressJob;
bool nand_address_begin(NandAddressJob *, const NandAddressMap *, uint32_t media_offset);
/* Examine at most min(entry_budget,64) remaps. No hardware/OS calls or allocation. */
NandAddressStatus nand_address_step(NandAddressJob *, uint32_t entry_budget);
#endif
