#include "portable_reader_platform.h"
#include "nspire_hardware.h"
#include "arm926_ram_span.h"
#include "../player/native_interrupts.h"
#include "../player/storage_mutation.h"
#include "../sram.h"
#include <os.h>
#include <string.h>

#define EXPORT_ADDRESS(name, number)                                                               \
    static uint32_t export_##name(void)                                                            \
    {                                                                                              \
        unsigned saved = native_interrupt_mask();                                                  \
        register uint32_t value __asm__("r0");                                                     \
        __asm__ volatile("swi %[callnr]"                                                           \
                         : "=r"(value)                                                             \
                         : [callnr] "i"(__SYSCALLS_ISVAR | (number))                               \
                         : "r1", "r2", "r3", "r4", "r12", "lr", "memory", "cc");                   \
        uint32_t address = value;                                                                  \
        native_critical_leave(saved);                                                              \
        return address;                                                                            \
    }
EXPORT_ADDRESS(fopen, e_fopen)
EXPORT_ADDRESS(fread, e_fread)
EXPORT_ADDRESS(fwrite, e_fwrite)
EXPORT_ADDRESS(fclose, e_fclose)
EXPORT_ADDRESS(malloc, e_malloc)
EXPORT_ADDRESS(current_task, e_TCC_Current_Task_Pointer)
EXPORT_ADDRESS(errno_addr, e_errno_addr)

static uint32_t word(uint32_t address, void *unused)
{
    (void)unused;
    return *(const volatile uint32_t *)(uintptr_t)address;
}
static bool allow(void *context, uint32_t address, uint32_t bytes, unsigned kind)
{
    PortableReaderPlatform *p = context;
    (void)kind;
    return p->table_valid && arm926_ram_span(address, bytes, p->table_alias, word, NULL, NULL);
}
static bool read_word(void *context, uint32_t address, uint32_t *value)
{
    if (!value || !allow(context, address, 4U, PORTABLE_DATA))
        return false;
    *value = word(address, NULL);
    return true;
}
static const uint32_t *code_words(void *context, uint32_t address, uint32_t bytes)
{
    PortableReaderPlatform *p = context;
    if ((address & 3U) || !bytes || (bytes & 3U) || native_interrupt_mask() != 0xc0U ||
        p->view.code_end <= p->view.code_begin || bytes > p->view.code_end - p->view.code_begin ||
        address < p->view.code_begin || address > p->view.code_end - bytes ||
        !allow(context, address, bytes, PORTABLE_CODE)) return NULL;
    return (const uint32_t *)(uintptr_t)address;
}
static bool callable(PortableReaderPlatform *p, uint32_t address)
{
    return !(address & 3U) && address >= p->view.code_begin && address <= p->view.code_end - 4U &&
           allow(p, address, 4U, PORTABLE_CODE);
}
bool portable_reader_platform_refresh(PortableReaderPlatform *p)
{
    if (!p)
        return false;
    p->table_valid = false;
    if (native_interrupt_mask() != 0xc0U) {
        p->status = PORTABLE_READER_PLATFORM_MASK;
        return false;
    }
    __asm__ volatile("mrc p15,0,%0,c1,c0,0" : "=r"(p->control));
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(p->ttbr));
    p->ttbr &= 0xffffc000U;
    __asm__ volatile("mrc p15,0,%0,c3,c0,0" : "=r"(p->dacr));
    unsigned domain = p->dacr & 3U;
    p->status = PORTABLE_READER_PLATFORM_MAPPING;
    if (!(p->control & 1U) || (domain != 1U && domain != 3U))
        return false;
    p->table_alias = p->ttbr;
    if (sram_uses_native_clone()) {
        /* A400 is the player's authoritative uncached native clone. Reading
         * the ordinary cached heap alias can miss native L1 updates. */
        if (p->ttbr != (sram_expected_ttbr() & 0xffffc000U) || p->ttbr < 0x10000000U ||
            p->ttbr > 0x13ffc000U)
            return false;
        uint32_t alias = word(0xa4004000U + 0xa40U * 4U, NULL);
        if ((alias & 0x1e3U) != 2U || !(alias & 0xc00U) ||
            (alias & 0xfff00000U) + 0x4000U != p->ttbr)
            return false;
        p->table_alias = 0xa4004000U;
    } else if (!((p->ttbr >= 0x10000000U && p->ttbr <= 0x13ffc000U) ||
                 (p->ttbr >= 0xa4000000U && p->ttbr <= 0xa400c000U)))
        return false;
    /* Ndless's loader itself relies on a readable table identity alias.
     * Verify the complete backing section before following descriptors. */
    uint32_t self = word(p->table_alias + (p->ttbr >> 20) * 4U, NULL);
    if ((self & 0x1e3U) != 2U || !(self & 0xc00U) ||
        (self & 0xfff00000U) != (p->ttbr & 0xfff00000U))
        return false;
    p->table_valid = true;
    p->view =
        (PortableView){p, allow, read_word, 0x10000000U, 0x12000000U, 0x10000000U, 0x14000000U, code_words};
    p->status = PORTABLE_READER_PLATFORM_OK;
    return true;
}
const PortableView *portable_reader_platform_view(PortableReaderPlatform *p)
{
    return p && p->table_valid && native_interrupt_mask() == 0xc0U ? &p->view : NULL;
}
int portable_reader_platform_init(PortableReaderPlatform *p)
{
    if (!p)
        return PORTABLE_READER_PLATFORM_ARGUMENT;
    if (p->initialized)
        return PORTABLE_READER_PLATFORM_OK;
    memset(p, 0, sizeof(*p));
    unsigned saved = native_interrupt_mask();
    if (!(saved & 0x80U))
        return p->status = PORTABLE_READER_PLATFORM_MASK;
    p->asic = word(0x900a0000U, NULL);
    if (nspire_asic_is_cx(p->asic))
        p->kind = NAND_PAGE_CX_PL351;
    else if (p->asic == 0x202U)
        p->kind = NAND_PAGE_CX2_SPI;
    else
        return p->status = PORTABLE_READER_PLATFORM_HARDWARE;
    p->anchors = (PortableAnchors){
        export_fopen(),  export_fread(),        export_fwrite(),     export_fclose(),
        export_malloc(), export_current_task(), export_errno_addr(), 0};
    native_critical_enter();
    if (portable_reader_platform_refresh(p)) {
        portable_resolve_exports(&p->view, &p->anchors, &p->exports);
        const uint32_t required = PORTABLE_FD_TABLE | PORTABLE_VFS_ROOTS;
        bool endpoints = callable(p, p->anchors.fopen) && callable(p, p->anchors.fclose) &&
                         callable(p, p->anchors.errno_addr);
        if ((p->exports.capabilities & required) == required && endpoints)
            p->initialized = true;
        else
            p->status = PORTABLE_READER_PLATFORM_EXPORTS;
    }
    native_critical_leave(saved);
    return p->status;
}
int portable_reader_platform_capture(PortableReaderPlatform *p, void *stream, uint32_t position,
                                     PortableStorageSnapshot *snapshot)
{
    if (!p || !p->initialized || !stream || !snapshot)
        return PORTABLE_READER_PLATFORM_ARGUMENT;
    if (!portable_reader_platform_refresh(p))
        return p->status;
    /* Code identity/export roots are immutable for this app instance. Start
     * fresh per-view counters; the live mounted objects are always recaptured. */
    p->last_resolved = p->exports;
    p->last_resolved.reads = 0;
    p->last_resolved.errors = 0;
    p->last_resolved.last_rejected = 0;
    return portable_storage_capture(&p->view, &p->last_resolved, (uint32_t)(uintptr_t)stream,
                                    position, p->kind, snapshot);
}
static int native_error(PortableReaderPlatform *p)
{
    /* Trusted Ndless ABI supplies a task-local errno pointer. Validate the
     * returned RAM address under the current mapping before reading it. */
    native_critical_enter();
    if (!portable_reader_platform_refresh(p) || !callable(p, p->anchors.errno_addr))
        return 0;
    int *address = ((int *(*)(void))(uintptr_t)p->anchors.errno_addr)();
    native_critical_enter();
    if (((uintptr_t)address & 3U) || !portable_reader_platform_refresh(p) ||
        !allow(p, (uint32_t)(uintptr_t)address, 4U, PORTABLE_DATA))
        return 0;
    return *(const volatile int *)address;
}
void *portable_reader_platform_open(PortableReaderPlatform *p, const char *path)
{
    if (!p || !p->initialized || !path)
        return NULL;
    unsigned saved = native_critical_enter();
    p->native_error = 0;
    if (!portable_reader_platform_refresh(p) || !callable(p, p->anchors.fopen)) {
        if (!p->status)
            p->status = PORTABLE_READER_PLATFORM_EXPORTS;
        native_critical_leave(saved);
        return NULL;
    }
    native_critical_leave(saved);
    storage_mutation_invalidate();
    void *stream = ((void *(*)(const char *, const char *))(uintptr_t)p->anchors.fopen)(path, "rb");
    p->native_error = stream ? 0 : native_error(p);
    native_critical_leave(saved);
    p->table_valid = false;
    p->status = stream ? PORTABLE_READER_PLATFORM_OK : PORTABLE_READER_PLATFORM_OPEN;
    return stream;
}
int portable_reader_platform_close(PortableReaderPlatform *p, void *stream)
{
    if (!p || !p->initialized || !stream)
        return PORTABLE_READER_PLATFORM_ARGUMENT;
    unsigned saved = native_critical_enter();
    p->native_error = 0;
    if (!portable_reader_platform_refresh(p) || !callable(p, p->anchors.fclose)) {
        if (!p->status)
            p->status = PORTABLE_READER_PLATFORM_EXPORTS;
        native_critical_leave(saved);
        return p->status;
    }
    native_critical_leave(saved);
    storage_mutation_invalidate();
    int result = ((int (*)(void *))(uintptr_t)p->anchors.fclose)(stream);
    p->native_error = result ? native_error(p) : 0;
    native_critical_leave(saved);
    p->table_valid = false;
    return result;
}
int portable_reader_platform_errno(const PortableReaderPlatform *p)
{
    return p ? p->native_error : 0;
}
static uint32_t mmio_read32(void *unused, uint32_t address)
{
    return word(address, unused);
}
static uint8_t mmio_read8(void *unused, uint32_t address)
{
    (void)unused;
    return *(const volatile uint8_t *)(uintptr_t)address;
}
static void mmio_write32(void *unused, uint32_t address, uint32_t value)
{
    (void)unused;
    *(volatile uint32_t *)(uintptr_t)address = value;
}
NandPageBus portable_reader_platform_bus(void)
{
    return (NandPageBus){NULL, mmio_read32, mmio_read8, mmio_write32};
}
