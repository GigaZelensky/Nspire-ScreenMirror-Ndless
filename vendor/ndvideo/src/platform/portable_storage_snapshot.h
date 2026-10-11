#ifndef NDVIDEO_PORTABLE_STORAGE_SNAPSHOT_H
#define NDVIDEO_PORTABLE_STORAGE_SNAPSHOT_H
#include "portable_resolver.h"
#include "../storage/nand_page_reader.h"
#include "../storage/nand_address.h"
#define PORTABLE_STORAGE_DIRTY_MAX 32U
#define PORTABLE_STORAGE_CLEAN_MAX 128U
#define PORTABLE_STORAGE_ANY_POSITION UINT32_MAX
typedef struct {
    uint32_t number, address, flags;
} PortableStorageBlock;
typedef struct {
    uint32_t address, id;
    bool active;
} PortableStorageRegion;
enum {
    PORTABLE_STORAGE_OK = 0,
    PORTABLE_STORAGE_ARGUMENT = -1,
    PORTABLE_STORAGE_EXPORTS = -2,
    PORTABLE_STORAGE_STREAM = -3,
    PORTABLE_STORAGE_VFS = -4,
    PORTABLE_STORAGE_RELIANCE = -5,
    PORTABLE_STORAGE_HANDLE = -6,
    PORTABLE_STORAGE_VOLUME = -7,
    PORTABLE_STORAGE_CACHE = -8,
    PORTABLE_STORAGE_FLASHFX = -9,
    PORTABLE_STORAGE_PHYSICAL = -10,
    PORTABLE_STORAGE_MAPPING = -11,
    PORTABLE_STORAGE_LIMIT = -12
};
typedef struct {
    int status;
    uint32_t fault, reads, spans;
    uint32_t stream, posix_fd, native_fd, handle, mount, operations;
    uint32_t inode, position, file_bytes, volume, volume_id, block_bytes, block_count, index_block;
    uint32_t disk, state, fim, context, physical_callback;
    uint32_t cache_nodes, dirty_count, clean_count, regions_active;
    uint32_t tag_column, ecc_first, ecc_second;
    NandPageKind kind;
    NandSpareLayout spare;
    NandAddressMap layout;
    uint8_t meta[64], state_data[2688], remaps[4096];
    PortableStorageBlock dirty[PORTABLE_STORAGE_DIRTY_MAX], clean[PORTABLE_STORAGE_CLEAN_MAX];
    PortableStorageRegion region[3];
} PortableStorageSnapshot;
/* Pure bounded observation. Caller authenticates the active MMU in PortableView
 * and excludes native filesystem/driver activity throughout capture and use.
 * The native stream stays open; no file read/seek/write is called here.
 * Pass0 after fresh rb open, ANY_POSITION for an independently observed live
 * stream. Position is diagnostic; raw requests use their own logical offset.
 * kind must come from an independently validated hardware identification.
 * Borrowed block/region addresses expire when native storage may run. Snapshot
 * must stay at a stable address because layout.remaps refers to its owned bytes. */
int portable_storage_capture(const PortableView *, PortableResolved *, uint32_t native_stream,
                             uint32_t expected_position, NandPageKind kind,
                             PortableStorageSnapshot *);
bool portable_storage_copy(const PortableView *, uint32_t address, void *destination,
                           uint32_t bytes);
#endif
