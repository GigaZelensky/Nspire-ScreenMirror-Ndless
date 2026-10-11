#ifndef NDVIDEO_PORTABLE_RESOLVER_H
#define NDVIDEO_PORTABLE_RESOLVER_H
#include <stdbool.h>
#include <stdint.h>
/* Find filesystem metadata through Ndless exports and checked ARM code.
 * Caller supplies a stable mapping view and excludes native storage while
 * reading discovered objects. No discovered function pointer is invoked. */
enum { PORTABLE_CODE = 1, PORTABLE_DATA = 2 };
typedef struct {
    void *context;
    bool (*allow_span)(void *, uint32_t address, uint32_t bytes, unsigned kind);
    bool (*read_word)(void *, uint32_t address, uint32_t *value);
    uint32_t code_begin, code_end, ram_begin, ram_end;
    /* Optional mapped, validated code span. Borrow only while the caller keeps
     * IRQ/FIQ excluded; discard before any native call or progress callback. */
    const uint32_t *(*code_words)(void *, uint32_t address, uint32_t bytes);
} PortableView;
typedef struct {
    uint32_t fopen, fread, fwrite, fclose, malloc, current_task, errno_addr, read_nand;
} PortableAnchors;
enum {
    /* Includes verified native stream fd+4 and read-mode+12 prefix. */
    PORTABLE_FD_TABLE = 1U,
    PORTABLE_TASK_GLOBAL = 2U,
    PORTABLE_HEAP_GLOBAL = 4U,
    PORTABLE_VFS_ROOTS = 8U,
    PORTABLE_RELIANCE_ROOTS = 16U,
    PORTABLE_FLASHFX_ROOTS = 32U
};
enum {
    PORTABLE_BAD_VIEW = 1U,
    PORTABLE_ACCESS = 2U,
    PORTABLE_LIMIT = 4U,
    PORTABLE_NO_MATCH = 8U,
    PORTABLE_AMBIGUOUS = 16U,
    PORTABLE_INCONSISTENT = 32U
};
typedef struct {
    uint32_t capabilities, errors, reads, last_rejected;
    uint32_t fd_lookup, fd_table, task_global, heap_global;
    uint32_t native_read, vfs_table;
    uint32_t reliance_read, rel_handles_global, rel_volumes_global;
    uint32_t rel_active_volume_global, rel_cache_global;
    uint32_t flashfx_read, ffx_disks_global, ffx_devices_table, ffx_device_slots;
} PortableResolved;
/* Starts a fresh result. Export anchors come from the installed Ndless table,
 * acquired outside the view, with exact IRQ/FIQ mask restored afterward. */
uint32_t portable_resolve_exports(const PortableView *, const PortableAnchors *,
                                  PortableResolved *);
/* read_callback is obtained from the validated mount operations table, not a
 * guessed OS address. Each function also recognizes the complete code family. */
bool portable_resolve_reliance(const PortableView *, uint32_t read_callback, PortableResolved *);
bool portable_resolve_flashfx(const PortableView *, uint32_t read_callback, PortableResolved *);
#endif
