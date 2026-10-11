#ifndef NDVIDEO_STORAGE_MUTATION_H
#define NDVIDEO_STORAGE_MUTATION_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#define STORAGE_MUTATION_BLOCKS 1024U
/* Read only while storage is excluded. Epoch 0 permanently disables reuse.
 * Generations identify possible physical NAND mutations, not successful writes.
 * Ordinary native/standby paths outside the observed private writer MUST call
 * invalidate. Geometry, BBM mapping and region membership need separate checks. */
uint32_t storage_mutation_epoch(void);
uint32_t storage_mutation_block_generation(uint32_t raw_block);
void storage_mutation_invalidate(void);
/* descriptor is a checked five-word native SPI descriptor. Unknown shapes,
 * setup/reset/feature writes fail closed. This never issues a device command. */
bool storage_mutation_observe_spi(uint32_t command, const uint32_t descriptor[5]);
void storage_mutation_callback_failed(void);
typedef struct {
    uint32_t observed, programs, erases, unknown, failures;
} StorageMutationStats;
StorageMutationStats storage_mutation_stats(void);
size_t storage_mutation_memory_bytes(void);
#endif
