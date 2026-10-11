#include "performance_clock.h"
#include "../platform/nspire_hardware.h"
#include "native_interrupts.h"
#include <stdint.h>

#define CLOCK_WORD(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define CLOCK_PLL 0x90140030U
#define CLOCK_DIVIDER 0x90140020U
#define CLOCK_PENDING 0x90140024U
#define CLOCK_SOURCE 0x90140810U
#define CLOCK_PLL_FIELDS 0xFF1F0311U
#define CLOCK_DIVIDER_FIELDS 0x00F00000U
#define CX_CLOCK_LOAD 0x900B0000U
#define CX_CLOCK_PENDING 0x900B0014U
#define CX_CLOCK_CURRENT 0x900B0024U
#define CX_CLOCK_FIELDS 0xC07FF1FEU

static unsigned preference_mhz;
static bool preference_keep_after_exit;

static struct {
    bool supported, owned, active;
    uint32_t asic, target_hz;
    uint32_t target_pll;
    uint32_t saved_pll, saved_divider, entry_hz, requests;
    uint32_t native_calls, native_entry;
    uint32_t saved_emi74, saved_emi1c, native_hash;
    int status;
} clock_state;

/* Keep startup evidence separate from the live state: a later manual Apply
 * must not erase an early refusal or a clock overwritten during setup. */
static struct {
    bool begun, completed, entry_valid, first_ok, final_ok;
    uint32_t saved_pll, saved_divider, saved_emi74, saved_emi1c, entry_hz;
    uint32_t first_hz, before_final_hz, final_hz, attempts;
    int first_status, final_status;
} startup_clock;

static uint32_t cx_cpu_hz(uint32_t config)
{
    if (config & 0x100U)
        return 48000000U >> (config >> 30);
    uint32_t divider = config & 0xFEU;
    /* Nover 3's CX CPU mode: mode 1 halves the ordinary even divisor.
     * Multiplier 40, divisor 1 => 240 MHz; AHB /4 => 60 MHz (not 120).
     * This mode is omitted by older emulator clock-rate calculations. */
    if (((config >> 21) & 3U) == 1U)
        divider >>= 1;
    return divider ? (6000000U * ((config >> 15) & 63U)) / divider : 0U;
}

static uint32_t cpu_hz(void)
{
    if (nspire_asic_is_cx(clock_state.asic))
        return cx_cpu_hz(CLOCK_WORD(CX_CLOCK_CURRENT));
    uint32_t pll = CLOCK_WORD(CLOCK_PLL), divider = CLOCK_WORD(CLOCK_DIVIDER);
    if (!(CLOCK_WORD(CLOCK_SOURCE) & 0x10U)) return 48000000U;
    if (pll & 0x10U) return 24000000U;
    uint32_t input_divider = (pll >> 16) & 31U;
    if (!(pll & 1U) || !input_divider) return 0;
    /* The OS CPU query uses a 24 MHz reference. Nover II's BASE=12 display
     * reports a different rate; 41 / 2 with no final divider is 492 MHz CPU. */
    return (24000000U / input_divider) * ((pll >> 24) & 63U) /
           (((divider >> 20) & 15U) + 1U);
}

static bool native_mapping(void)
{
    uint32_t table;
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(table));
    table &= 0xFFFFC000U;
    if (!((table >= 0x10000000U && table <= 0x13FFC000U) ||
          (table >= 0xA4000000U && table <= 0xA400C000U)))
        return false;
    uint32_t sram = CLOCK_WORD(table + 0xA40U * 4U);
    return (sram & 3U) == 2U && (sram & 0xFFF00000U) == 0xA4000000U;
}

/* Native SRAM copy of the OS's complete PLL/EMI transition. The checked
 * block includes the worker, self-refresh helpers and their literal pool.
 * This validates actual instructions, not an OS version number. */
#define CX2_CLOCK_BLOCK 0xA40012C8U
#define CX2_CLOCK_BLOCK_BYTES 0x258U
#define CX2_CLOCK_BLOCK_FNV 0x8D914E2FU
#define CX2_CLOCK_ENTRY 0xA4001444U
#define EMI_CONTROL 0x90120004U
#define EMI_TIMING74 0x90120074U
#define EMI_TIMING1C 0x9012001CU

static uint32_t native_clock_hash(void)
{
    const volatile uint8_t *code = (const volatile uint8_t *)(uintptr_t)CX2_CLOCK_BLOCK;
    uint32_t hash = 2166136261U;
    for (unsigned i = 0; i < CX2_CLOCK_BLOCK_BYTES; ++i)
        hash = (hash ^ code[i]) * 16777619U;
    return hash;
}

static bool cx2_clock_control_idle(uint32_t control)
{
    /* The PMU control word also retains the last command's upper bits.
     * Native clock change writes 0x10000100; standby writes 0x80000008.
     * Their request bits self-clear, leaving 0x10000000 or 0x80000000.
     * Neither upper value is a frequency divider. Accept the idle states,
     * including reset, while still refusing an outstanding request. */
    uint32_t command = control & ~CLOCK_DIVIDER_FIELDS;
    return command == 0U || command == 0x10000000U || command == 0x80000000U;
}

typedef struct {
    uint32_t pll, control, timing74, timing1c, emi_control;
} ClockReadback;

typedef struct {
    ClockReadback initial, final;
    uint32_t pll, divider, timing74, timing1c;
    uint32_t initial_mask, final_mask, polls;
    bool restoring;
} ClockVerification;

/* Retain the first unsettled return even if later retries or manual Apply
 * succeed. A rollback otherwise replaces the evidence with the entry clock. */
static struct {
    unsigned delayed, failed;
    ClockVerification first, last;
} clock_verification;

static uint32_t cx2_clock_readback(ClockReadback *state, uint32_t pll,
    uint32_t divider, uint32_t timing74, uint32_t timing1c)
{
    state->pll = CLOCK_WORD(CLOCK_PLL);
    state->control = CLOCK_WORD(CLOCK_DIVIDER);
    state->timing74 = CLOCK_WORD(EMI_TIMING74);
    state->timing1c = CLOCK_WORD(EMI_TIMING1C);
    state->emi_control = CLOCK_WORD(EMI_CONTROL);
    return (((state->pll & CLOCK_PLL_FIELDS) != pll) ? 1U : 0U) |
        (((state->control & CLOCK_DIVIDER_FIELDS) != divider) ? 2U : 0U) |
        (!cx2_clock_control_idle(state->control) ? 4U : 0U) |
        ((state->timing74 != timing74) ? 8U : 0U) |
        ((state->timing1c != timing1c) ? 16U : 0U) |
        ((state->emi_control & 0x40CU) ? 32U : 0U);
}

static bool cx2_clock_verify(uint32_t pll, uint32_t divider,
    uint32_t timing74, uint32_t timing1c, bool restoring)
{
    ClockVerification check = {0};
    check.initial_mask = cx2_clock_readback(&check.initial, pll, divider, timing74, timing1c);
    if (!check.initial_mask) return true;

    check.final = check.initial;
    check.final_mask = check.initial_mask;
    check.pll = pll;
    check.divider = divider;
    check.timing74 = timing74;
    check.timing1c = timing1c;
    check.restoring = restoring;
    check.polls = 1U;
    /* The SRAM worker waits for the EMI exit request to clear, not for all
     * of these readbacks. Keep the same predicates while allowing settling.
     * The 32 kHz timer may be restored/stopped at a standby boundary, so an
     * iteration limit also bounds the wait when that timer is unavailable. */
    bool timed = (CLOCK_WORD(0x900C0008U) & 0xCFU) == 0x82U &&
                 CLOCK_WORD(0x900C0080U) == 0x0AU;
    uint32_t started = timed ? CLOCK_WORD(0x900C0004U) : 0U;
    while (check.final_mask && check.polls < 4096U) {
        if (timed && started - CLOCK_WORD(0x900C0004U) >= 66U) break;
        check.final_mask = cx2_clock_readback(&check.final, pll, divider, timing74, timing1c);
        ++check.polls;
    }
    if (!clock_verification.delayed && !clock_verification.failed)
        clock_verification.first = check;
    clock_verification.last = check;
    if (check.final_mask) ++clock_verification.failed;
    else ++clock_verification.delayed;
    return check.final_mask == 0U;
}

static bool apply_cx2(uint32_t pll_fields, uint32_t divider_fields, bool restoring)
{
    unsigned saved = native_critical_enter();
    bool ok = false;
    if (!native_mapping()) { clock_state.status = -1; goto done; }
    /* The worker applies divider zero. Check the actual divider separately
     * from the last-command bits, so a normal standby return remains usable. */
    uint32_t control = CLOCK_WORD(CLOCK_DIVIDER);
    if (divider_fields || (control & CLOCK_DIVIDER_FIELDS) || !cx2_clock_control_idle(control)) {
        clock_state.status = -11; goto done;
    }
    clock_state.native_hash = native_clock_hash();
    if (clock_state.native_hash != CX2_CLOCK_BLOCK_FNV) {
        clock_state.status = -12; goto done;
    }
    if (!(CLOCK_WORD(CLOCK_SOURCE) & 0x10U) ||
        (CLOCK_WORD(EMI_CONTROL) & 0x40CU) ||
        (CLOCK_WORD(0xDC00000CU) & (1U << 15))) {
        clock_state.status = -13; goto done;
    }
    /* Standby/display activity can leave PMU status pending. The OS's other
     * clock leaf (1000029c in the checked driver) acknowledges old status
     * before its clock request. Do the same, then check the actual IRQ line:
     * status bits alone do not imply a live interrupt or a failed restore. */
    uint32_t pending = CLOCK_WORD(CLOCK_PENDING);
    if (pending) CLOCK_WORD(CLOCK_PENDING) = pending;
    __asm__ volatile("mcr p15,0,%0,c7,c10,4" :: "r"(0) : "memory");
    unsigned wait = 64U;
    while ((CLOCK_WORD(0xDC000008U) & (1U << 15)) && --wait) { }
    if (!wait) { clock_state.status = -13; goto done; }
    uint32_t pll = (CLOCK_WORD(CLOCK_PLL) & ~CLOCK_PLL_FIELDS) | pll_fields;
    /* All selectable CX II rates use the OS's highest timing profile.
     * Only an explicit restore uses the entry timings. The same frequency
     * can have different timings after the OS ran with a retained overclock. */
    uint32_t timing74 = restoring ? clock_state.saved_emi74 : 0x55U;
    uint32_t timing1c = restoring ? clock_state.saved_emi1c : 0x528U;
    ++clock_state.requests;
    ++clock_state.native_calls;
    /* CPU IRQ/FIQ stay masked: IRQ15 wakes WFI without entering the OS.
     * The SRAM worker enters DRAM self-refresh, commits the PLL through
     * 0x90140020, updates EMI timings, exits self-refresh and acknowledges
     * the PMU. It touches neither the DRAM stack nor app code in that span. */
    ((void (*)(uint32_t, uint32_t, uint32_t))(uintptr_t)CX2_CLOCK_ENTRY)(pll, timing74, timing1c);
    ok = cx2_clock_verify(pll_fields, divider_fields, timing74, timing1c, restoring);
    clock_state.status = ok ? 0 : -4;
done:
    native_critical_leave(saved);
    return ok;
}

/* Locate the native SRAM clock leaf by its complete code/helper/literal
 * image, rather than by an OS version or a fixed entry address. All accesses
 * between SDRAM self-refresh entry and exit remain in SRAM or MMIO. */
#define CX_CLOCK_WORKER_BYTES 496U
#define CX_CLOCK_WORKER_FNV 0xBF6499C3U
static uintptr_t cx_clock_worker(void)
{
    for (uintptr_t at = 0xA4000000U; at <= 0xA400C000U - CX_CLOCK_WORKER_BYTES; at += 4U) {
        if (CLOCK_WORD(at) != 0xE92D4010U || CLOCK_WORD(at + 4U) != 0xE1A02001U ||
            CLOCK_WORD(at + 8U) != 0xE1A03000U || CLOCK_WORD(at + 12U) != 0xE3A00000U)
            continue;
        const volatile uint8_t *code = (const volatile uint8_t *)at;
        uint32_t hash = 2166136261U;
        for (unsigned i = 0; i < CX_CLOCK_WORKER_BYTES; ++i)
            hash = (hash ^ code[i]) * 16777619U;
        clock_state.native_hash = hash;
        clock_state.native_entry = (uint32_t)at;
        if (hash == CX_CLOCK_WORKER_FNV) {
            return at;
        }
    }
    return 0;
}

static bool apply_cx(uint32_t fields)
{
    unsigned saved = native_critical_enter();
    bool ok = false;
    if (!native_mapping()) { clock_state.status = -1; goto done; }
    uint32_t load = CLOCK_WORD(CX_CLOCK_LOAD);
    if ((CLOCK_WORD(CX_CLOCK_CURRENT) & CX_CLOCK_FIELDS) == fields &&
        (load & CX_CLOCK_FIELDS) == fields && !(CLOCK_WORD(CX_CLOCK_PENDING) & 2U)) {
        clock_state.status = 0;
        ok = true;
        goto done;
    }
    uintptr_t entry = cx_clock_worker();
    if (!entry) { clock_state.status = -12; goto done; }
    uint32_t control;
    __asm__ volatile("mrc p15,0,%0,c1,c0,0" : "=r"(control));
    if (!(control & 1U) || (CLOCK_WORD(0x8FFF0000U) & 3U) != 1U ||
        (CLOCK_WORD(0x900B0018U) & 0x200000U) ||
        (CLOCK_WORD(0xDC00000CU) & (1U << 15))) {
        clock_state.status = -13; goto done;
    }
    uint32_t requested = (load & ~CX_CLOCK_FIELDS) | fields;
    ++clock_state.requests;
    ++clock_state.native_calls;
    /* This native leaf prepares SDRAM,
     * enables only the PMU wake source, commits through WFI, acknowledges it,
     * and restores SDRAM/MMU/interrupt-controller state before returning.
     * Argument zero preserves the existing SDRAM refresh configuration. */
    ((void (*)(uint32_t, uint32_t))entry)(0U, requested);
    ok = !(CLOCK_WORD(CX_CLOCK_PENDING) & 2U) &&
        (CLOCK_WORD(CX_CLOCK_CURRENT) & CX_CLOCK_FIELDS) == fields &&
        (CLOCK_WORD(CX_CLOCK_LOAD) & CX_CLOCK_FIELDS) == fields;
    clock_state.status = ok ? 0 : -4;
done:
    native_critical_leave(saved);
    return ok;
}

static bool apply(uint32_t pll_fields, uint32_t divider_fields, bool restoring)
{
    return nspire_asic_is_cx(clock_state.asic) ? apply_cx(pll_fields)
                                    : apply_cx2(pll_fields, divider_fields, restoring);
}

bool performance_clock_start(void)
{
    clock_state.asic = CLOCK_WORD(0x900A0000U);
    bool cx = nspire_asic_is_cx(clock_state.asic);
    clock_state.supported = cx || clock_state.asic == 0x202U;
    if (!clock_state.supported) return false;
    unsigned mhz = preference_mhz ? preference_mhz : (cx ? 132U : 396U);
    clock_state.target_hz = mhz * 1000000U;
    if (!performance_clock_valid_mhz(preference_mhz)) return false;
    if (!clock_state.owned) {
        clock_state.saved_pll = cx ? CLOCK_WORD(CX_CLOCK_CURRENT) & CX_CLOCK_FIELDS
                                  : CLOCK_WORD(CLOCK_PLL) & CLOCK_PLL_FIELDS;
        clock_state.saved_divider = cx ? 0U : CLOCK_WORD(CLOCK_DIVIDER) & CLOCK_DIVIDER_FIELDS;
        clock_state.entry_hz = cpu_hz();
        if (!cx) {
            clock_state.saved_emi74 = CLOCK_WORD(EMI_TIMING74);
            clock_state.saved_emi1c = CLOCK_WORD(EMI_TIMING1C);
        }
        clock_state.owned = true;
    }
    /* Keep the CX bus at the fastest integral divisor within 66 MHz.
     * A blanket /4 unnecessarily slows memory at modest CPU overclocks.
     * Derive this from the rate itself so Test's explicit 132 MHz choice
     * and the default-speed sentinel select identical hardware settings. */
    unsigned cx_ahb_divisor = (mhz + 65U) / 66U;
    uint32_t target = cx
        ? ((mhz / 6U) << 15) | (1U << 21) | (1U << 1) | ((cx_ahb_divisor - 1U) << 12) | (clock_state.saved_pll & 0xC0000000U)
        : ((mhz / 12U) << 24) | (2U << 16) | 0x301U;
    clock_state.target_pll = target;
    clock_state.active = apply(target, 0U, false) && cpu_hz() == clock_state.target_hz;
    if (!clock_state.active) {
        int failure = clock_state.status ? clock_state.status : -5;
        if (performance_clock_restore())
            clock_state.status = failure;
    }
    return clock_state.active;
}

static bool selected_clock_matches(void)
{
    if (!clock_state.supported || !clock_state.owned || !clock_state.active ||
        cpu_hz() != clock_state.target_hz) return false;
    if (nspire_asic_is_cx(clock_state.asic))
        return (CLOCK_WORD(CX_CLOCK_CURRENT) & CX_CLOCK_FIELDS) == clock_state.target_pll &&
               (CLOCK_WORD(CX_CLOCK_LOAD) & CX_CLOCK_FIELDS) == clock_state.target_pll &&
               !(CLOCK_WORD(CX_CLOCK_PENDING) & 2U);
    uint32_t control = CLOCK_WORD(CLOCK_DIVIDER);
    return (CLOCK_WORD(CLOCK_PLL) & CLOCK_PLL_FIELDS) == clock_state.target_pll &&
           !(control & CLOCK_DIVIDER_FIELDS) && cx2_clock_control_idle(control) &&
           !(CLOCK_WORD(CLOCK_PENDING) & 1U) &&
           CLOCK_WORD(EMI_TIMING74) == 0x55U && CLOCK_WORD(EMI_TIMING1C) == 0x528U &&
           !(CLOCK_WORD(EMI_CONTROL) & 0x40CU);
}

void performance_clock_startup_begin(void)
{
    if (startup_clock.begun) return;
    startup_clock.begun = true;
    startup_clock.first_ok = performance_clock_start();
    startup_clock.first_status = clock_state.status;
    startup_clock.first_hz = clock_state.supported ? cpu_hz() : 0U;
    /* start() may have rolled back and relinquished ownership. Retain the
     * original snapshot so a delayed startup retry still restores the clock
     * that was present on entry, not an intervening setup/USB clock choice. */
    startup_clock.entry_valid = clock_state.supported && clock_state.entry_hz != 0U;
    startup_clock.saved_pll = clock_state.saved_pll;
    startup_clock.saved_divider = clock_state.saved_divider;
    startup_clock.saved_emi74 = clock_state.saved_emi74;
    startup_clock.saved_emi1c = clock_state.saved_emi1c;
    startup_clock.entry_hz = clock_state.entry_hz;
}

bool performance_clock_startup_complete(void)
{
    if (!startup_clock.begun) performance_clock_startup_begin();
    if (startup_clock.completed) return startup_clock.final_ok;
    startup_clock.before_final_hz = clock_state.supported ? cpu_hz() : 0U;
    bool ok = selected_clock_matches();
    /* A settled native startup boundary, not a retry in every frame. Keep
     * the same fingerprint/mapping/idle guards as manual Apply. */
    while (!ok && clock_state.supported && startup_clock.attempts < 2U) {
        if (!clock_state.owned && startup_clock.entry_valid) {
            clock_state.saved_pll = startup_clock.saved_pll;
            clock_state.saved_divider = startup_clock.saved_divider;
            clock_state.saved_emi74 = startup_clock.saved_emi74;
            clock_state.saved_emi1c = startup_clock.saved_emi1c;
            clock_state.entry_hz = startup_clock.entry_hz;
            clock_state.owned = true;
        }
        ++startup_clock.attempts;
        ok = performance_clock_start();
    }
    startup_clock.completed = true;
    startup_clock.final_ok = ok;
    startup_clock.final_status = clock_state.status;
    startup_clock.final_hz = clock_state.supported ? cpu_hz() : 0U;
    return ok;
}

bool performance_clock_restore(void)
{
    if (!clock_state.owned) return true;
    for (unsigned attempt = 0; attempt < 2U; ++attempt) {
        if (!apply(clock_state.saved_pll, clock_state.saved_divider, true))
            continue;
        clock_state.owned = false;
        clock_state.active = false;
        return true;
    }
    /* Keep ownership on failure so a later lifecycle cleanup can retry. */
    return false;
}

bool performance_clock_finish(bool normal_exit)
{
    if (!normal_exit || !preference_keep_after_exit)
        return performance_clock_restore();
    /* Final cleanup may have used native display/power routines. Verify the
     * selected clock again before handing it to the OS without restoring. */
    if (!performance_clock_start()) return false;
    clock_state.owned = clock_state.active = false;
    return true;
}

static void clock_verification_debug(FILE *file, const char *name, const ClockVerification *check)
{
    fprintf(file,
        "performance_clock_verify_%s restoring=%u polls=%lu initial_mask=%02lx final_mask=%02lx target_pll=%08lx target_divider=%08lx target_emi74=%08lx target_emi1c=%08lx initial_pll=%08lx initial_control=%08lx initial_emi74=%08lx initial_emi1c=%08lx initial_emi_control=%08lx final_pll=%08lx final_control=%08lx final_emi74=%08lx final_emi1c=%08lx final_emi_control=%08lx\n",
        name, check->restoring, (unsigned long)check->polls,
        (unsigned long)check->initial_mask, (unsigned long)check->final_mask,
        (unsigned long)check->pll, (unsigned long)check->divider,
        (unsigned long)check->timing74, (unsigned long)check->timing1c,
        (unsigned long)check->initial.pll, (unsigned long)check->initial.control,
        (unsigned long)check->initial.timing74, (unsigned long)check->initial.timing1c,
        (unsigned long)check->initial.emi_control,
        (unsigned long)check->final.pll, (unsigned long)check->final.control,
        (unsigned long)check->final.timing74, (unsigned long)check->final.timing1c,
        (unsigned long)check->final.emi_control);
}

void performance_clock_debug(FILE *file)
{
    if (!file) return;
    bool cx = nspire_asic_is_cx(clock_state.asic);
    uint32_t hz = clock_state.supported ? cpu_hz() : 0U;
    uint32_t config = clock_state.supported ? CLOCK_WORD(cx ? CX_CLOCK_CURRENT : CLOCK_PLL) : 0U;
    uint32_t pending = clock_state.supported ? CLOCK_WORD(cx ? CX_CLOCK_PENDING : CLOCK_PENDING) : 0U;
    uint32_t ahb_divisor = cx && !(config & 0x100U) ? ((config >> 12) & 7U) + 1U : 2U;
    bool configured_active = clock_state.active && hz == clock_state.target_hz &&
                             !(pending & (cx ? 2U : 1U));
    fprintf(file, "performance_clock supported=%u active=%u status=%d entry_hz=%lu target_hz=%lu register_cpu_hz=%lu ahb_hz=%lu asic=%lx pll=%08lx divider=%08lx source=%08lx pending=%08lx requests=%lu\n",
            clock_state.supported, configured_active, clock_state.status,
            (unsigned long)clock_state.entry_hz, (unsigned long)clock_state.target_hz,
            (unsigned long)hz, (unsigned long)(hz / ahb_divisor), (unsigned long)clock_state.asic,
            (unsigned long)config,
            (unsigned long)(clock_state.supported ? (cx ? config & 0x7000U : CLOCK_WORD(CLOCK_DIVIDER)) : 0U),
            (unsigned long)(clock_state.supported ? CLOCK_WORD(cx ? CX_CLOCK_LOAD : CLOCK_SOURCE) : 0U),
            (unsigned long)pending,
            (unsigned long)clock_state.requests);
    if (cx && clock_state.supported)
        fprintf(file, "performance_clock_native calls=%lu code_hash=%08lx entry=%08lx\n",
                (unsigned long)clock_state.native_calls, (unsigned long)clock_state.native_hash,
                (unsigned long)clock_state.native_entry);
    else if (clock_state.supported)
        fprintf(file, "performance_clock_native calls=%lu code_hash=%08lx emi74=%08lx emi1c=%08lx\n",
                (unsigned long)clock_state.native_calls, (unsigned long)clock_state.native_hash,
                (unsigned long)CLOCK_WORD(EMI_TIMING74), (unsigned long)CLOCK_WORD(EMI_TIMING1C));
    if (startup_clock.begun)
        fprintf(file, "performance_clock_startup first_ok=%u first_status=%d first_hz=%lu before_final_hz=%lu completed=%u attempts=%lu final_ok=%u final_status=%d final_hz=%lu entry_hz=%lu\n",
                startup_clock.first_ok, startup_clock.first_status, (unsigned long)startup_clock.first_hz,
                (unsigned long)startup_clock.before_final_hz, startup_clock.completed,
                (unsigned long)startup_clock.attempts, startup_clock.final_ok, startup_clock.final_status,
                (unsigned long)startup_clock.final_hz, (unsigned long)startup_clock.entry_hz);
    if (clock_verification.delayed || clock_verification.failed) {
        fprintf(file, "performance_clock_verify delayed=%u failed=%u mask_bits=1:pll,2:divider,4:control,8:emi74,16:emi1c,32:emi_busy\n",
            clock_verification.delayed, clock_verification.failed);
        clock_verification_debug(file, "first", &clock_verification.first);
        if (clock_verification.delayed + clock_verification.failed > 1U)
            clock_verification_debug(file, "last", &clock_verification.last);
    }
}

/* Pending choices belong to this app session. Only the normal main() exit
 * commits them; a reset during Test or playback cannot save a new choice. */
unsigned performance_clock_selection(void) { return preference_mhz; }
bool performance_clock_keep_after_exit(void) { return preference_keep_after_exit; }
unsigned performance_clock_current_mhz(void) { return clock_state.supported ? cpu_hz() / 1000000U : 0U; }
unsigned performance_clock_max_mhz(void) { return clock_state.asic == 0x202U ? 492U : 240U; }
unsigned performance_clock_default_mhz(void) { return clock_state.asic == 0x202U ? 396U : 132U; }
unsigned performance_clock_option_count(void) { return (performance_clock_max_mhz() - performance_clock_default_mhz()) / 12U + 1U; }
unsigned performance_clock_option(unsigned index)
{
    return performance_clock_default_mhz() + index * 12U;
}
bool performance_clock_valid_mhz(unsigned mhz)
{
    if (!mhz) return true;
    bool cx2 = CLOCK_WORD(0x900A0000U) == 0x202U;
    return mhz % 12U == 0U && mhz >= (cx2 ? 396U : 132U) && mhz <= (cx2 ? 492U : 240U);
}
bool performance_clock_choose(unsigned mhz, bool keep_after_exit)
{
    if (!performance_clock_valid_mhz(mhz)) return false;
    unsigned previous = preference_mhz;
    if (mhz == performance_clock_default_mhz()) mhz = 0;
    preference_mhz = mhz;
    if (!performance_clock_start()) {
        preference_mhz = previous;
        performance_clock_start();
        return false;
    }
    preference_keep_after_exit = keep_after_exit;
    return true;
}
void performance_clock_load(unsigned mhz, bool keep_after_exit)
{
    bool valid = performance_clock_valid_mhz(mhz);
    preference_mhz = valid ? mhz : 0;
    preference_keep_after_exit = valid && keep_after_exit;
}

extern void performance_clock_benchmark_loop(unsigned iterations);
static uint32_t benchmark(void)
{
    if ((CLOCK_WORD(0x900C0008U) & 0xCFU) != 0x82U || !(CLOCK_WORD(0x900C0080U) & 2U)) return 0;
    unsigned saved = native_critical_enter();
    uint32_t samples[3];
    performance_clock_benchmark_loop(256U);
    for (unsigned i = 0; i < 3U; ++i) {
        uint32_t started = CLOCK_WORD(0x900C0004U);
        performance_clock_benchmark_loop(131072U);
        samples[i] = started - CLOCK_WORD(0x900C0004U);
    }
    native_critical_leave(saved);
    if (!samples[0] || !samples[1] || !samples[2] || samples[0] > 32768U || samples[1] > 32768U || samples[2] > 32768U) return 0;
    if (samples[0] > samples[1]) { uint32_t t = samples[0]; samples[0] = samples[1]; samples[1] = t; }
    if (samples[1] > samples[2]) { uint32_t t = samples[1]; samples[1] = samples[2]; samples[2] = t; }
    return samples[0] > samples[1] ? samples[0] : samples[1];
}
bool performance_clock_test(unsigned mhz, uint32_t *before, uint32_t *after)
{
    unsigned previous = preference_mhz;
    *before = *after = 0;
    if (!performance_clock_valid_mhz(mhz) || !performance_clock_restore()) return false;
    preference_mhz = 0;
    if (performance_clock_start()) *before = benchmark();
    bool baseline_restored = performance_clock_restore();
    preference_mhz = mhz;
    bool applied = baseline_restored && *before && performance_clock_start();
    if (applied) *after = benchmark();
    bool restored = performance_clock_restore();
    preference_mhz = previous;
    bool resumed = restored && performance_clock_start();
    return applied && *after && resumed;
}

int performance_clock_status(void) { return clock_state.status; }
