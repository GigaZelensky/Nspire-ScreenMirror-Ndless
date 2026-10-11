#ifndef NDVIDEO_NAND_PAGE_READER_H
#define NDVIDEO_NAND_PAGE_READER_H
#include "cx_nand_reader.h"
#include "spi_nand_reader.h"

/* Page reader for CX and CX II. MMIO addresses are absolute for both
 * backends; it never substitutes native filesystem/NAND read calls.
 */
typedef CxNandBus NandPageBus;
typedef enum { NAND_PAGE_CX_PL351, NAND_PAGE_CX2_SPI } NandPageKind;
typedef enum {
    NAND_SPARE_CX2_ONDIE_TAG4,
    NAND_SPARE_CX_BOOT512,
    NAND_SPARE_CX_FLASHFX256_TAG12
} NandSpareLayout;
typedef struct {
    NandPageKind kind;
    NandPageBus bus;
    NandSpareLayout spare;
} NandPageConfig;
typedef enum {
    NAND_PAGE_IDLE,
    NAND_PAGE_PENDING,
    NAND_PAGE_DONE,
    NAND_PAGE_CANCELED,
    NAND_PAGE_ERROR
} NandPageStatus;
typedef struct {
    NandPageConfig config;
    union {
        CxNandReader cx;
        SpiNandReader spi;
    } engine;
    void *destination;
    uint32_t page, column, bytes, started, timeout;
    NandPageStatus status;
    unsigned error;
    bool initialized, probing, quiescent, corrected;
} NandPageReader;
bool nand_page_init(NandPageReader *, NandPageConfig);
/* Zero means this profile has no supported FlashFX tag mapping. */
uint32_t nand_page_tag_column(const NandPageReader *);
bool nand_page_begin_range(NandPageReader *, uint32_t page, uint32_t column, void *destination,
                           size_t bytes, uint32_t now, uint32_t timeout_ticks);
void nand_page_cancel(NandPageReader *);
NandPageStatus nand_page_step(NandPageReader *, uint32_t now, uint32_t byte_budget);
#endif
