#ifndef NDVIDEO_SPI_NAND_READER_H
#define NDVIDEO_SPI_NAND_READER_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* Read-only CX II SPI NAND engine. Exclusive controller ownership must cover
 * begin through terminal status; bus callbacks must be bounded MMIO accesses.
 * No OS API, allocator, busy wait, or flash program/erase operation is used.
 * The caller must keep servicing cancellation until quiescent is true. */
typedef struct {
    void *context;
    uint32_t (*read32)(void *, uint32_t);
    uint8_t (*read8)(void *, uint32_t);
    void (*write32)(void *, uint32_t, uint32_t);
} SpiNandBus;
/* Optional receive-only DMA transport. begin returns 1 with ownership, 0 for
 * a legitimate PIO case, -1 for failure without ownership. A started command is never mixed with
 * PIO: failed DMA is retired and the SPI controller reset before reporting an error.
 * poll: 0 pending, 1 complete, -1 error. release returns true only once DMA is
 * idle, its channel restored, and destination cache ownership returned to CPU. */
typedef struct {
    void *context;
    int (*begin)(void *, void *destination, uint32_t bytes);
    int (*poll)(void *);
    void (*abort)(void *, bool failed);
    bool (*release)(void *, bool completed);
} SpiNandDma;
typedef enum {
    SPI_NAND_IDLE,
    SPI_NAND_PENDING,
    SPI_NAND_DONE,
    SPI_NAND_CANCELED,
    SPI_NAND_ERROR
} SpiNandStatus;
typedef enum {
    SPI_NAND_OK,
    SPI_NAND_BAD_ARGUMENT,
    SPI_NAND_NOT_IDLE,
    SPI_NAND_TIMEOUT,
    SPI_NAND_ECC,
    SPI_NAND_RESET_FAILED,
    SPI_NAND_DMA_ERROR,
    SPI_NAND_DMA_STOP_FAILED
} SpiNandError;
typedef struct {
    SpiNandBus bus;
    SpiNandDma dma;
    uint8_t *destination;
    uint32_t page, copied, started, timeout, reset_started, saved_enable, saved_control,
        last_status;
    uint32_t fifo_depth, rx_credit, initial_status, initial_features, initial_interrupt;
    uint32_t column, requested;
    uint32_t fifo_word, fifo_bytes;
    unsigned phase;
    SpiNandStatus status;
    SpiNandError error;
    /* quiescent describes the SPI controller, not a guarantee that a failed
     * NAND chip is ready. Native code also polls chip readiness before use. */
    bool cancel, quiescent, corrected;
    bool dma_active, reset_for_dma;
    bool wide_enabled, profile_ready, id_received;
    uint8_t wide_mode, feature_a0, feature_b0, profile_reason;
    uint32_t chip_id, single_commands, dual_commands, quad_commands;
} SpiNandReader;

bool spi_nand_init(SpiNandReader *, SpiNandBus);
bool spi_nand_set_dma(SpiNandReader *, SpiNandDma);
bool spi_nand_enable_wide(SpiNandReader *);
bool spi_nand_begin(SpiNandReader *, uint32_t page, void *destination, size_t bytes, uint32_t now,
                    uint32_t timeout_ticks);
/* Bounded slice of a 2048+64-byte physical page, including metadata/spare. */
bool spi_nand_begin_range(SpiNandReader *, uint32_t page, uint32_t column, void *destination,
                          size_t bytes, uint32_t now, uint32_t timeout_ticks);
void spi_nand_cancel(SpiNandReader *);
/* At most 128 FIFO bytes per call, regardless of budget. A timeout uses the
 * same timeout_ticks for bounded controller-reset cleanup. Caller also needs
 * an independent watchdog if its supplied clock can stop. */
SpiNandStatus spi_nand_step(SpiNandReader *, uint32_t now, uint32_t byte_budget);
#endif
