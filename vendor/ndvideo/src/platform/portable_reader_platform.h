#ifndef NDVIDEO_PORTABLE_READER_PLATFORM_H
#define NDVIDEO_PORTABLE_READER_PLATFORM_H
#include "portable_storage_snapshot.h"

enum {
    PORTABLE_READER_PLATFORM_OK = 0,
    PORTABLE_READER_PLATFORM_ARGUMENT = -1300,
    PORTABLE_READER_PLATFORM_MASK = -1301,
    PORTABLE_READER_PLATFORM_MAPPING = -1302,
    PORTABLE_READER_PLATFORM_HARDWARE = -1303,
    PORTABLE_READER_PLATFORM_EXPORTS = -1304,
    PORTABLE_READER_PLATFORM_OPEN = -1305
};
typedef struct {
    PortableAnchors anchors;
    PortableResolved exports, last_resolved;
    PortableView view;
    NandPageKind kind;
    uint32_t asic, control, ttbr, table_alias, dacr;
    int status, native_error;
    bool initialized, table_valid;
} PortableReaderPlatform;

/* Stable-address object: view.context refers back to this instance. Init
 * obtains trusted Ndless export addresses, never calls native file reads. */
int portable_reader_platform_init(PortableReaderPlatform *);
/* Refresh/capture/view use requires IRQ+FIQ exclusion AND exclusive storage
 * ownership. Borrowed snapshot addresses expire before native storage runs. */
bool portable_reader_platform_refresh(PortableReaderPlatform *);
const PortableView *portable_reader_platform_view(PortableReaderPlatform *);
int portable_reader_platform_capture(PortableReaderPlatform *, void *native_stream,
                                     uint32_t expected_position, PortableStorageSnapshot *);
/* Caller first drains/parks the primary reader and app writers (normally
 * raw_player_before_native()). These direct metadata operations invalidate
 * raw map proofs and restore the exact incoming IRQ/FIQ mask. No writer
 * capability is granted by this platform adapter. */
void *portable_reader_platform_open(PortableReaderPlatform *, const char *path);
int portable_reader_platform_close(PortableReaderPlatform *, void *native_stream);
int portable_reader_platform_errno(const PortableReaderPlatform *);
NandPageBus portable_reader_platform_bus(void);
#endif
