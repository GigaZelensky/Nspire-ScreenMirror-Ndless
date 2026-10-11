#ifndef NDVIDEO_SRAM_H
#define NDVIDEO_SRAM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool sram_init(void);
void sram_shutdown(void);
/* Foreground, with all app workers joined. Temporarily restore native SRAM
 * for a returning OS operation, retaining every pool pointer and byte.
 * The callback must not access the decoder pool or start app workers. */
bool sram_with_native_mapping(void (*operation)(void *), void *context);
void *sram_alloc(size_t size, size_t alignment);
bool sram_is_enabled(void);
/* Whether the CX II clone is currently mapped, excluding native callbacks.
 * Original CX only borrows its identity tail and never maps a clone. */
bool sram_uses_native_clone(void);
size_t sram_bytes_used(void);
size_t sram_bytes_capacity(void);
uint32_t sram_active_ttbr(void);
uint32_t sram_expected_ttbr(void);
const char *sram_status_message(void);

#endif
