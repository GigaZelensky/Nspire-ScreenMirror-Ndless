#ifndef NDVIDEO_CX_NAND_READER_H
#define NDVIDEO_CX_NAND_READER_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Read-only PL351 interface 0 transport. No native calls, allocations,
 * program/erase/reset, ECC configuration, interrupt enable or timing writes.
 * The owner must exclude every other NAND user until quiescent. Each step is
 * bounded; a chip that never becomes ready cannot be safely handed back.
 * All bus addresses are absolute; each callback must be a bounded MMIO access.
 */
typedef struct {
    void *context;
    uint32_t (*read32)(void *, uint32_t);
    uint8_t (*read8)(void *, uint32_t);
    void (*write32)(void *, uint32_t, uint32_t);
} CxNandBus;
typedef enum {
    CX_NAND_IDLE,
    CX_NAND_PENDING,
    CX_NAND_DONE,
    CX_NAND_CANCELED,
    CX_NAND_ERROR
} CxNandStatus;
typedef enum {
    CX_NAND_OK,
    CX_NAND_BAD_ARGUMENT,
    CX_NAND_UNSUPPORTED_CONTROLLER,
    CX_NAND_UNSUPPORTED_ECC_MODE,
    CX_NAND_UNSUPPORTED_GEOMETRY,
    CX_NAND_NOT_IDLE,
    CX_NAND_TIMEOUT,
    CX_NAND_ECC,
    CX_NAND_CHIP_FAILURE
} CxNandError;
typedef struct {
    uint32_t asic, memc_status, memif_cfg, ecc_status, ecc_config;
    uint8_t peripheral_id[4];
} CxNandSnapshot;
typedef enum { CX_NAND_ECC_BOOT512, CX_NAND_ECC_FLASHFX256 } CxNandLayout;
typedef struct {
    CxNandBus bus;
    CxNandSnapshot snapshot;
    uint8_t id[4], data[2112];
    uint8_t *destination;
    uint32_t page, column, bytes, position, started, timeout;
    uint32_t read_column, transfer_bytes;
    uint32_t corrected_data, corrected_codes, last_chip_status;
    unsigned phase, sector;
    CxNandStatus status;
    CxNandError error;
    CxNandLayout layout;
    bool initialized, identified, cancel, quiescent, identifying, raw_tag;
} CxNandReader;

/* Passive reads only; false does not issue a NAND command. */
bool cx_nand_init(CxNandReader *, CxNandBus);
bool cx_nand_init_layout(CxNandReader *, CxNandBus, CxNandLayout);
/* Status/READID only. Legacy 128 MiB x8 SLC, 2 KiB+64 pages, 128 KiB erase blocks.
 * Requires exclusive ownership, caller-supplied monotonic wrap-safe clock.
 */
bool cx_nand_identify(CxNandReader *, uint32_t now, uint32_t timeout_ticks);
bool cx_nand_begin_range(CxNandReader *, uint32_t page, uint32_t column, void *destination,
                         size_t bytes, uint32_t now, uint32_t timeout_ticks);
/* FLASHFX256's exact spare range column2060/bytes4 uses a raw tag-only transfer.
 * FlashFX owns its independent Hamming/check-byte validation; data-sector
 * ECC does not cover these bytes. Every other range reads/checks the fullpage.
 */
void cx_nand_cancel(CxNandReader *);
/* Up to 128 payload bytes or one ECC sector per step. No busy waits.
 * Timeout/cancel keeps polling status until ready and chip select released.
 * ERROR+!quiescent is a retained-ownership failure; continue servicing to
 * permit eventual cleanup. No destination bytes are published before ECC.
 */
CxNandStatus cx_nand_step(CxNandReader *, uint32_t now, uint32_t byte_budget);
/* Independent software ECC, matching TI's legacy 3-byte-per512 OOB layout.
 * 0 clean;1 corrected data;2 single ECC-code bit;negative uncorrectable.
 */
void cx_nand_ecc512(const uint8_t data[512], uint8_t code[3]);
int cx_nand_correct512(uint8_t data[512], const uint8_t code[3]);
void cx_nand_ecc256(const uint8_t data[256], uint8_t code[3]);
int cx_nand_correct256(uint8_t data[256], const uint8_t code[3]);
#endif
