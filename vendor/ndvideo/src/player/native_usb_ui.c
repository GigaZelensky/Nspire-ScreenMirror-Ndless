#include "native_usb_ui.h"
#include "../storage/usb/usb_registers.h"
#include "native_usb_ui_resolver.h"
#include "native_interrupts.h"
#include "../platform/portable_reader_platform.h"
#include <limits.h>

#define WORD(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define HALF(a) (*(volatile uint16_t *)(uintptr_t)(a))
static NativeUsbUiApi api;
static PortableReaderPlatform mapping;
static bool resolved, input_held, apd_held, busy_held, display_held, cursor_held;
static unsigned scheduler_waits;
static unsigned reprobe_count;
static int reprobe_result;
static unsigned interface_reprobe_count;
static int interface_reprobe_result;
static uint32_t interface_reprobe_handle, interface_reprobe_descriptor;
static unsigned interface_reprobe_config, interface_reprobe_expected, interface_reprobe_interfaces;
/* The callback validates identity-mapped DRAM. The caller's native stack is
 * not guaranteed to meet that requirement. Both BSD locks serialize this
 * temporary argument block, including across the native attach call's waits. */
static uint32_t reprobe_args[12];
static unsigned start_count;
static int start_result;
static int start_cleanup_result;
static unsigned enumeration_count;
static int enumeration_result;
static bool memory(uint32_t at, unsigned bytes);
static uint32_t original_input, original_tracking, original_display, original_busy, original_cursor;
static uint32_t original_apd_blocks = UINT32_MAX;
static const char *stage = "idle";
const char *native_usb_ui_stage(void)
{
    return stage;
}
typedef struct {
    uint32_t root, otg, hcd, bus, hub, hub_owner, hub_state, handle;
} UsbHostTree;

static bool method_is(uint32_t device, unsigned kind, unsigned identity, unsigned method,
                      unsigned role)
{
    if (!api.function[role] || !memory(device, 44)) return false;
    uint32_t driver = WORD(device), methods;
    if (!memory(driver, 36) || WORD(device + 12) != kind || WORD(driver + 4) != kind ||
        WORD(driver + 12) != identity) return false;
    methods = WORD(driver);
    return memory(methods, 28) && WORD(methods + method * 4) == api.function[role];
}
static bool host_tree(UsbHostTree *tree)
{
    *tree = (UsbHostTree){0};
    if (!api.bus_node || !memory(api.bus_node, 16) || WORD(api.bus_node) != (api.classic_cx ? 0x903U : 0x915U)) return false;
    tree->root = WORD(api.bus_node + 8);
    if (!method_is(tree->root, 1, api.classic_cx ? 0x903U : 0x915U, 1, UI_OTG_ATTACH)) return false;
    tree->otg = WORD(tree->root + 8);
    if (!memory(tree->otg, api.classic_cx ? 68 : 88) ||
        (!api.classic_cx && WORD(tree->otg + 64))) return false;
    /* The FOTG OTG wrapper owns separate device and EHCI children at 76/80.
     * TDI on original CX owns its EHCI child at 60. Neither wrapper is
     * itself the EHCI softc. Both constructors are witnessed. */
    tree->hcd = WORD(tree->otg + (api.classic_cx ? 60 : 80));
    if (!method_is(tree->hcd, 1, api.classic_cx ? 4 : 21, 5, UI_HCD_CONTROL)) return false;
    tree->bus = WORD(tree->hcd + 8);
    if (!memory(tree->bus, 1804) || WORD(tree->bus) != tree->hcd ||
        WORD(tree->bus + 1768) != tree->root || WORD(tree->bus + 608) != (api.classic_cx ? 64U : 16U)) return false;
    uint32_t registers = WORD(tree->bus + 596);
    if (!memory(registers, 12) || WORD(registers + 8) != (api.classic_cx ? 0xb0000100U : 0xb0000000U)) return false;
    tree->hub = WORD(tree->bus + 48);
    if (!memory(tree->hub, 112) || WORD(tree->hub) != tree->bus) return false;
    tree->hub_owner = WORD(tree->hub + 100);
    if (!method_is(tree->hub_owner, 2, 0, 5, UI_HUB_CONTROL)) return false;
    tree->hub_state = WORD(tree->hub_owner + 8);
    return memory(tree->hub_state, 44) && WORD(tree->hub_state) == tree->hub_owner &&
           WORD(tree->hub_state + 4) == tree->hub;
}

static uint32_t code_branch(uint32_t at)
{
    const PortableView *v = portable_reader_platform_view(&mapping);
    if (!v || (at & 3U) || !v->allow_span(v->context, at, 4, PORTABLE_CODE)) return 0;
    uint32_t op = WORD(at);
    if ((op & 0x0e000000U) != 0x0a000000U) return 0;
    uint32_t target = at + 8 + ((int32_t)(op << 8) >> 6);
    return v->allow_span(v->context, target, 4, PORTABLE_CODE) ? target : 0;
}

static uint32_t resume_method(uint32_t device, unsigned role)
{
    const PortableView *v = portable_reader_platform_view(&mapping);
    uint32_t methods = WORD(WORD(device)), function = WORD(methods + 16);
    return native_usb_ui_role_matches(v, function, role) ? function : 0;
}

static bool resume_tree(UsbHostTree *tree, uint32_t *resume)
{
    if (!host_tree(tree)) return false;
    tree->handle = WORD(tree->bus + 4);
    uint32_t methods = WORD(tree->bus + 8);
    if (!memory(tree->handle, 36) || WORD(tree->handle) != tree->hcd ||
        WORD(tree->handle + 4) != tree->bus || WORD(tree->handle + 16) != tree->hub ||
        WORD(tree->handle + 32) > 1 || !memory(methods, 36) ||
        !api.function[UI_EHCI_WAKE] || WORD(methods + 32) != api.function[UI_EHCI_WAKE] ||
        !api.function[UI_BUS_RESUME] || !api.function[UI_BUS_WAKE]) return false;
    *resume = resume_method(tree->hcd, UI_HCD_RESUME);
    if (!*resume || !resume_method(tree->hub_owner, UI_HUB_RESUME) ||
        code_branch(*resume + 16) != api.function[UI_BUS_RESUME] ||
        code_branch(api.function[UI_BUS_RESUME] + 32) != api.function[UI_BUS_WAKE]) return false;
    /* Structural validation is shared by start and resume. A fully stopped
     * bus legitimately has active=0; only resume requires active=1. */
    return WORD(tree->bus + 1772) <= 1 &&
           WORD(tree->bus + 1760) <= 1 &&
           WORD(tree->bus + 1760) == WORD(tree->handle + 32);
}

static int cold_start_check(const UsbHostTree *tree)
{
    /* CX II can retain an initialized, root-only bus after the OS rejects a
     * peripheral, with no registered HCD interrupt and no suspend flag. The
     * resume method cannot start that bus. Control 18 registers its interrupt
     * and starts the existing schedules; it does not allocate a second bus. */
    if (api.classic_cx || WORD(tree->bus + 604) || WORD(tree->handle + 32) ||
        WORD(tree->hub_state + 36)) return -31;
    /* bus+1744 is the native free-transfer pool, not an active-transfer list.
     * Cached objects there are valid even before the controller starts. */
    const PortableView *v = portable_reader_platform_view(&mapping);
    uint32_t control = api.function[UI_HCD_CONTROL];
    uint32_t core = code_branch(control + 40);
    if (!native_usb_ui_role_matches(v, core, UI_HCD_CORE_CONTROL) ||
        code_branch(control + 188) != core ||
        !native_usb_ui_role_matches(v, code_branch(core + 248), UI_EHCI_START) ||
        !native_usb_ui_role_matches(v, code_branch(control + 124), UI_IRQ_REGISTER)) return -32;
    uint32_t irq = WORD(tree->bus + 600), registers = WORD(tree->bus + 596);
    if (!memory(irq, 20) || WORD(irq) != 2 ||
        *(const volatile uint8_t *)(uintptr_t)(irq + 4) != 1 ||
        WORD(irq + 8) >= 128 || !memory(registers, 20) || WORD(registers) != 2 ||
        *(const volatile uint8_t *)(uintptr_t)(registers + 4) != 2) return -33;
    uint32_t dma = WORD(tree->bus + 660), head = WORD(tree->bus + 1740);
    uint32_t count = WORD(tree->bus + 668);
    if ((count != 256 && count != 512 && count != 1024) ||
        !memory(dma, 8) || !memory(head, 80) ||
        !memory(WORD(tree->bus + 664), count * 4)) return -34;
    uint32_t periodic = WORD(dma + 4), async = WORD(head + 76);
    if ((periodic & 4095U) || (async & 31U) ||
        !memory(periodic, count * 4) || !memory(async, 64) ||
        WORD(head) != (async | 2U) || !(WORD(head + 16) & 1U) ||
        (WORD(head + 24) & 0x80U)) return -35;
    /* Never start over a child device or a linked asynchronous transfer. */
    bool root_found = false;
    for (unsigned slot = 1; slot < 128; ++slot) {
        uint32_t device = WORD(tree->bus + 52 + slot * 4);
        if (!device) continue;
        if (device != tree->hub || root_found) return -36;
        root_found = true;
    }
    return root_found ? 0 : -38;
}

static bool storage_configuration(uint32_t device)
{
    const PortableView *v = portable_reader_platform_view(&mapping);
    uint32_t address = WORD(device + 84);
    if (!address || !v->allow_span(v->context, address, 9, PORTABLE_DATA)) return false;
    const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t)address;
    unsigned size = p[2] | (unsigned)p[3] << 8;
    if (p[0] != 9 || p[1] != 2 || size < 9 ||
        !v->allow_span(v->context, address, size, PORTABLE_DATA)) return false;
    for (unsigned at = 9; at < size;) {
        if (size - at < 2 || p[at] < 2 || p[at] > size - at) return false;
        if (p[at + 1] == 4 && p[at] >= 9 && p[at + 5] == 8 &&
            p[at + 6] == 6 && p[at + 7] == 0x50) return true;
        at += p[at];
    }
    return false;
}

static int configured_storage_interface(uint32_t device, uint32_t *interface,
                                        unsigned *configuration, unsigned *number)
{
    const PortableView *v = portable_reader_platform_view(&mapping);
    uint32_t config = WORD(device + 84), iface = WORD(device + 60);
    interface_reprobe_handle = iface;
    interface_reprobe_descriptor = 0;
    interface_reprobe_config = *(const volatile uint8_t *)(uintptr_t)(device + 9);
    interface_reprobe_expected = interface_reprobe_interfaces = 0;
    if (!memory(config, 9)) return -44;
    const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t)config;
    interface_reprobe_expected = p[5];
    interface_reprobe_interfaces = p[4];
    unsigned size = p[2] | (unsigned)p[3] << 8;
    if (p[0] != 9 || p[1] != 2 || p[4] != 1 || !p[5] || size < 18 ||
        *(const volatile uint8_t *)(uintptr_t)(device + 9) != p[5] ||
        !v->allow_span(v->context, config, size, PORTABLE_DATA)) return -44;
    /* USBdi's configured interface array has 32-byte elements. Bind only a
     * single, unclaimed interface with no association or open native pipes. */
    if (!memory(iface, 32) || WORD(iface) != device || WORD(iface + 4) ||
        WORD(iface + 28)) return -45;
    uint32_t descriptor = WORD(iface + 8);
    interface_reprobe_descriptor = descriptor;
    for (unsigned at = 9; at < size;) {
        if (size - at < 2 || p[at] < 2 || p[at] > size - at) return -46;
        if (config + at == descriptor) {
            if (p[at] < 9 || p[at + 1] != 4 || p[at + 4] != 2 ||
                p[at + 5] != 8 || p[at + 6] != 6 || p[at + 7] != 0x50) return -46;
            *interface = iface;
            *configuration = p[5];
            *number = p[at + 2];
            return 0;
        }
        at += p[at];
    }
    return -46;
}

static int enumerate_empty_root(const UsbHostTree *tree, const UsbRegisters *registers)
{
    /* Resume deliberately scans with explore(hub, 0), which cannot discover
     * a new device. Handle a connected device on an otherwise empty root
     * separately; never reset an existing child or another driver's device. */
    for (unsigned slot = 1; slot < 128; ++slot) {
        uint32_t child = WORD(tree->bus + 52 + slot * 4);
        if (child && child != tree->hub) return 0;
    }
    uint32_t port = WORD(registers->port);
    /* The OS may have acknowledged CONNECT_CHANGE before our driver was
     * registered. Current connection state plus the verified empty child
     * slots is what matters; requiring that transient flag strands the drive. */
    if (!(port & 1U)) return 0;
    const PortableView *v = portable_reader_platform_view(&mapping);
    uint32_t hub_resume = resume_method(tree->hub_owner, UI_HUB_RESUME);
    uint32_t explore = hub_resume ? code_branch(hub_resume + 36) : 0;
    if (!native_usb_ui_role_matches(v, explore, UI_HUB_EXPLORE)) return -40;
    uint32_t reset = code_branch(explore + 408);
    if (!native_usb_ui_role_matches(v, reset, UI_PORT_RESET)) return -41;
    uint32_t info = WORD(tree->hub + 96);
    if (!memory(info, 68) || WORD(info) != explore || WORD(info + 4) != tree->hub_state ||
        *(const volatile uint8_t *)(uintptr_t)(info + 10) != 1 || WORD(info + 56) ||
        *(const volatile uint8_t *)(uintptr_t)(tree->hub_state + 25) != 1 ||
        *(const volatile uint8_t *)(uintptr_t)(tree->hub_state + 26) ||
        *(const volatile uint8_t *)(uintptr_t)(tree->hub_state + 32) != 1 ||
        WORD(tree->hub_state + 36) != 1) return -42;
    ++enumeration_count;
    enumeration_result = 0;
    stage = "enumerate connected root port";
    if (!(port & 4U)) {
        /* The OTG root port is reset by its owner during normal insertion;
         * uhub_explore skips that reset for this port. Complete it through
         * USBdi before asking the hub to discover the connected device. */
        uint32_t status = 0;
        native_critical_leave(0);
        enumeration_result = ((int (*)(uint32_t, unsigned, void *))(uintptr_t)reset)(tree->hub, 1, &status);
        native_critical_enter();
        if (enumeration_result) return enumeration_result;
        UsbHostTree current;
        if (!portable_reader_platform_refresh(&mapping) || !host_tree(&current) ||
            current.hcd != tree->hcd || current.hub != tree->hub ||
            (WORD(registers->port) & 5U) != 5U ||
            (WORD(registers->otg) & registers->role_mask)) return -43;
    }
    native_critical_leave(0);
    enumeration_result = ((int (*)(uint32_t, unsigned))(uintptr_t)explore)(tree->hub, 1);
    native_critical_enter();
    return enumeration_result;
}

static void reprobe_storage(const UsbHostTree *tree)
{
    if (!api.function[UI_PROBE_DEVICE] || !api.function[UI_PROBE_BIND] ||
        !api.function[UI_NEW_DEVICE]) return;
    /* Match only an unclaimed direct-root high-speed storage device. Never
     * replace another driver's binding or manufacture USB endpoint state. */
    for (unsigned slot = 1; slot < 128; ++slot) {
        uint32_t device = WORD(tree->bus + 52 + slot * 4);
        if (!device || !memory(device, 112) || WORD(device) != tree->bus) continue;
        volatile uint8_t *bytes = (volatile uint8_t *)(uintptr_t)device;
        if (bytes[8] != slot || bytes[10] != 1 || bytes[11] != 3 ||
            bytes[64] != 18 || bytes[65] != 1 || bytes[108] != 2) continue;
        uint32_t owner = WORD(device + 100);
        if (!memory(owner, 44) || WORD(owner) || WORD(owner + 4) != tree->hub_owner ||
            WORD(owner + 20) || !storage_configuration(device)) continue;
        /* The witnessed native new-device routine supplies twelve words;
         * the older SDK attach-argument struct omits the final word. */
        if (!memory((uint32_t)(uintptr_t)reprobe_args, sizeof(reprobe_args))) {
            reprobe_result = -39;
            break;
        }
        const uint32_t args[12] = {1, UINT32_MAX, UINT32_MAX,
            bytes[72] | (uint32_t)bytes[73] << 8,
            bytes[74] | (uint32_t)bytes[75] << 8,
            bytes[76] | (uint32_t)bytes[77] << 8,
            device, 0, 0, 0, tree->hub_owner, UINT32_MAX};
        for (unsigned i = 0; i < 12; ++i) reprobe_args[i] = args[i];
        WORD(owner + 20) = (uint32_t)(uintptr_t)reprobe_args;
        stage = "match existing USB storage";
        ++reprobe_count;
        native_critical_leave(0);
        reprobe_result = ((int (*)(uint32_t, uint32_t))(uintptr_t)
            api.function[UI_PROBE_DEVICE])(owner, device);
        native_critical_enter();
        WORD(owner + 20) = 0;
        if (reprobe_result == 4 && portable_reader_platform_refresh(&mapping) &&
            memory(device, 112) && WORD(device + 100) == owner &&
            memory(owner, 44) && !WORD(owner) && !WORD(owner + 8) &&
            WORD(owner + 4) == tree->hub_owner && bytes[108] == 2) {
            /* Some OS states reject the generic whole-device driver before
             * it offers our driver an interface match. Use the already
             * configured native BOT interface; do not change configuration,
             * fabricate endpoints, or take interfaces from another driver. */
            uint32_t interface = 0;
            unsigned configuration = 0, number = 0;
            interface_reprobe_result = configured_storage_interface(device, &interface,
                                                                      &configuration, &number);
            if (!interface_reprobe_result) {
                for (unsigned i = 0; i < 12; ++i) reprobe_args[i] = args[i];
                reprobe_args[1] = configuration;
                reprobe_args[2] = number;
                reprobe_args[7] = interface;
                reprobe_args[8] = 1;
                WORD(owner + 20) = (uint32_t)(uintptr_t)reprobe_args;
                stage = "match configured storage interface";
                ++interface_reprobe_count;
                native_critical_leave(0);
                interface_reprobe_result = ((int (*)(uint32_t, uint32_t))(uintptr_t)
                    api.function[UI_PROBE_DEVICE])(owner, device);
                native_critical_enter();
                WORD(owner + 20) = 0;
                reprobe_result = interface_reprobe_result;
            }
        }
        if (!reprobe_result) bytes[108] = 0;
        break;
    }
}

static uint32_t apd_blocks(uint32_t *address)
{
    *address = 0;
    uint32_t function = api.function[UI_APD_ALLOW];
    if (!resolved || api.classic_cx || !memory(function + 4U, 4))
        return UINT32_MAX;
    /* The resolved CX II function's verified LDR loads its power-state object.
     * Observe its reference count without changing another owner's APD hold. */
    uint32_t instruction = WORD(function + 4U);
    if ((instruction & 0xfffff000U) != 0xe59f4000U)
        return UINT32_MAX;
    uint32_t literal = function + 12U + (instruction & 0xfffU);
    if (!memory(literal, 4)) return UINT32_MAX;
    uint32_t state = WORD(literal);
    if (!memory(state, 48) || WORD(state) != 1U) return UINT32_MAX;
    *address = state;
    return WORD(state + 44U);
}

void native_usb_ui_debug(FILE *file)
{
    if (!file)
        return;
    unsigned saved = native_critical_enter();
    (void)portable_reader_platform_refresh(&mapping);
    uint32_t power_state, power_blocks = apd_blocks(&power_state);
    uint32_t event_bits = api.event_mask && memory(api.event_mask & ~3U, 4)
        ? HALF(api.event_mask) & 31U : UINT32_MAX;
    uint32_t tracking_bit = api.tracking_flags && memory(api.tracking_flags & ~3U, 4)
        ? HALF(api.tracking_flags) & 8U : UINT32_MAX;
    uint32_t timer_control = WORD(0x900c0008U), timer_source = WORD(0x900c0080U);
    uint32_t lcd_flags = api.direct_lcd_flags && memory(api.direct_lcd_flags, 4) ? WORD(api.direct_lcd_flags) : 0;
    uint32_t bsd_mode = api.bsd_mode && memory(api.bsd_mode & ~3U, 4)
        ? *(const volatile uint8_t *)(uintptr_t)api.bsd_mode : UINT32_MAX;
    uint32_t devices[8][7];
    uint32_t lock_counts[2] = {UINT32_MAX, UINT32_MAX};
    if (api.bsd_locks && memory(api.bsd_locks, 12)) {
        for (unsigned i = 0; i < 2; ++i) {
            uint32_t semaphore = WORD(api.bsd_locks + 4 + i * 4);
            if (memory(semaphore, 40) && WORD(semaphore + 12) == 0x53454d41U)
                lock_counts[i] = WORD(semaphore + 24);
        }
    }
    unsigned device_count = 0;
    uint32_t host = 0, state = 0, pending = 0, started = 0, busy = 0, initialized = 0;
    if (api.host_global && memory(api.host_global, 4)) {
        host = WORD(api.host_global);
        if (memory(host, 16)) {
            pending = WORD(host + 8);
            state = WORD(host + 12);
            if (memory(state, 580)) {
                started = *(const volatile uint8_t *)(uintptr_t)(state + 544);
                busy = *(const volatile uint8_t *)(uintptr_t)(state + 545);
                initialized = WORD(state + 548);
            }
        }
    }
    UsbHostTree tree;
    bool tree_valid = host_tree(&tree);
    uint32_t root_device = tree.root, bus = tree_valid ? tree.bus : 0;
    uint32_t hcd_irq = bus ? WORD(bus + 604) : 0,
             hcd_active = bus ? WORD(bus + 1772) : 0,
             hcd_suspended = bus ? WORD(bus + 1760) : 0,
             bus_handle = bus ? WORD(bus + 4) : 0,
             bus_suspended = bus_handle && memory(bus_handle, 36) ? WORD(bus_handle + 32) : 0,
             hub_active = tree_valid ? WORD(tree.hub_state + 36) : 0,
             hub_port = tree_valid ? *(const volatile uint8_t *)(uintptr_t)(tree.hub_state + 32) : 0;
    uint32_t start_resources[12] = {0};
    uint32_t root_port[3] = {0};
    if (tree_valid) {
        uint32_t info = WORD(tree.hub + 96);
        if (memory(info, 68) && WORD(info + 4) == tree.hub_state &&
            *(const volatile uint8_t *)(uintptr_t)(info + 10) == 1) {
            root_port[0] = WORD(info + 48);
            root_port[1] = WORD(info + 56);
            root_port[2] = *(const volatile uint8_t *)(uintptr_t)(info + 64);
        }
    }
    if (bus && !hcd_irq) {
        uint32_t irq = WORD(bus + 600), registers = WORD(bus + 596);
        uint32_t dma = WORD(bus + 660), head = WORD(bus + 1740);
        if (memory(irq, 12)) {
            start_resources[0] = WORD(irq);
            start_resources[1] = WORD(irq + 4) & 255U;
            start_resources[2] = WORD(irq + 8);
        }
        if (memory(registers, 8)) {
            start_resources[3] = WORD(registers);
            start_resources[4] = WORD(registers + 4) & 255U;
        }
        start_resources[5] = WORD(bus + 668);
        start_resources[6] = WORD(bus + 664);
        start_resources[7] = memory(dma, 8) ? WORD(dma + 4) : 0;
        start_resources[8] = head;
        if (memory(head, 80)) {
            start_resources[9] = WORD(head + 76);
            start_resources[10] = WORD(head);
            start_resources[11] = WORD(head + 24);
        }
    }
    if (bus) {
        for (unsigned slot = 1; slot < 128 && device_count < 8; ++slot) {
            uint32_t device = WORD(bus + 52 + slot * 4);
            if (!device || !memory(device, 112) || WORD(device) != bus) continue;
            const volatile uint8_t *bytes = (const volatile uint8_t *)(uintptr_t)device;
            if (bytes[8] != slot) continue;
            uint32_t *entry = devices[device_count++];
            entry[0] = slot; entry[1] = device; entry[2] = bytes[11]; entry[3] = bytes[10];
            entry[4] = WORD(device + 96); entry[5] = WORD(device + 100); entry[6] = bytes[108];
        }
    }
    native_critical_leave(saved);
    fprintf(file, "usb_ui stage=%s resolved=%u classic_cx=%u input=%u apd=%u busy=%u display=%u cursor=%u\n",
            stage, resolved, api.classic_cx, input_held, apd_held, busy_held, display_held, cursor_held);
    fprintf(file, "usb_ui_power known=%u state=%08lx apd_blocks=%lu original_apd_blocks=%lu "
            "event_bits=%lu original_event_bits=%lu tracking_bit=%lu original_tracking_bit=%lu "
            "timer1_control=%08lx timer1_source=%08lx\n",
            power_blocks != UINT32_MAX, (unsigned long)power_state,
            (unsigned long)power_blocks, (unsigned long)original_apd_blocks,
            (unsigned long)event_bits, (unsigned long)original_input,
            (unsigned long)tracking_bit, (unsigned long)original_tracking,
            (unsigned long)timer_control, (unsigned long)timer_source);
    for (unsigned i = 0; i < UI_FUNCTIONS; ++i)
        fprintf(file, "usb_ui_function_%u=%08lx\n", i, (unsigned long)api.function[i]);
    fprintf(file,
            "usb_ui_display=%08lx state=%08lx busy=%08lx cursor_count=%08lx event_mask=%08lx "
            "tracking=%08lx direct_lcd_flags=%08lx\n",
            (unsigned long)api.display_descriptor, (unsigned long)api.display_state,
            (unsigned long)api.busy_base, (unsigned long)api.cursor_count,
            (unsigned long)api.event_mask, (unsigned long)api.tracking_flags, (unsigned long)lcd_flags);
    fprintf(file, "usb_ui_host global=%08lx root=%08lx state=%08lx pending=%08lx started=%lu busy=%lu initialized=%08lx\n",
            (unsigned long)api.host_global, (unsigned long)host, (unsigned long)state,
            (unsigned long)pending, (unsigned long)started, (unsigned long)busy,
            (unsigned long)initialized);
    fprintf(file, "usb_ui_bus node=%08lx root_device=%08lx bus=%08lx\n",
            (unsigned long)api.bus_node, (unsigned long)root_device, (unsigned long)bus);
    fprintf(file, "usb_ui_hcd valid=%u otg=%08lx device=%08lx bus=%08lx irq=%08lx active=%lu hub=%08lx hub_owner=%08lx hub_active=%lu port=%lu locks=%08lx\n",
            tree_valid, (unsigned long)tree.otg, (unsigned long)tree.hcd, (unsigned long)tree.bus,
            (unsigned long)hcd_irq, (unsigned long)hcd_active, (unsigned long)tree.hub,
            (unsigned long)tree.hub_owner, (unsigned long)hub_active, (unsigned long)hub_port,
            (unsigned long)api.bsd_locks);
    fprintf(file, "usb_ui_resume suspended=%lu bus_handle=%08lx bus_suspended=%lu reprobes=%u result=%d\n",
            (unsigned long)hcd_suspended, (unsigned long)bus_handle,
            (unsigned long)bus_suspended, reprobe_count, reprobe_result);
    fprintf(file, "usb_ui_interface_reprobe attempts=%u result=%d config=%u expected=%u interfaces=%u handle=%08lx descriptor=%08lx\n",
            interface_reprobe_count, interface_reprobe_result, interface_reprobe_config,
            interface_reprobe_expected, interface_reprobe_interfaces,
            (unsigned long)interface_reprobe_handle, (unsigned long)interface_reprobe_descriptor);
    fprintf(file, "usb_ui_lock_mode address=%08lx value=%08lx\n",
            (unsigned long)api.bsd_mode, (unsigned long)bsd_mode);
    fprintf(file, "usb_ui_locks usb_count=%lu tree_count=%lu\n",
            (unsigned long)lock_counts[0], (unsigned long)lock_counts[1]);
    fprintf(file, "usb_ui_start attempts=%u result=%d cleanup=%d\n",
            start_count, start_result, start_cleanup_result);
    fprintf(file, "usb_ui_enumerate attempts=%u result=%d\n", enumeration_count, enumeration_result);
    fprintf(file, "usb_ui_root_port cached_status_change=%08lx child=%08lx flags=%02lx\n",
            (unsigned long)root_port[0], (unsigned long)root_port[1], (unsigned long)root_port[2]);
    if (bus && !hcd_irq) fprintf(file,
        "usb_ui_start_resources irq_index=%lu irq_type=%lu irq_number=%lu mmio_index=%lu mmio_type=%lu "
        "frames=%lu periodic_va=%08lx periodic_pa=%08lx async_head=%08lx async_pa=%08lx async_link=%08lx token=%08lx\n",
        (unsigned long)start_resources[0], (unsigned long)start_resources[1],
        (unsigned long)start_resources[2], (unsigned long)start_resources[3],
        (unsigned long)start_resources[4], (unsigned long)start_resources[5],
        (unsigned long)start_resources[6], (unsigned long)start_resources[7],
        (unsigned long)start_resources[8], (unsigned long)start_resources[9],
        (unsigned long)start_resources[10], (unsigned long)start_resources[11]);
    for (unsigned i = 0; i < device_count; ++i) {
        const uint32_t *entry = devices[i];
        fprintf(file, "usb_ui_device slot=%lu device=%08lx speed=%lu depth=%lu hub=%08lx owner=%08lx state=%lu\n",
            (unsigned long)entry[0], (unsigned long)entry[1], (unsigned long)entry[2],
            (unsigned long)entry[3], (unsigned long)entry[4], (unsigned long)entry[5], (unsigned long)entry[6]);
    }
    fprintf(file,
            "usb_ui_scheduler waits=%u task_global=%08lx timer1=%08lx timer2=%08lx "
            "clock_source=%08lx irq_enabled=%08lx\n",
            scheduler_waits, (unsigned long)api.task_global, (unsigned long)WORD(0x900c0028U),
            (unsigned long)WORD(0x900d0008U), (unsigned long)WORD(0x900c0080U),
            (unsigned long)WORD(0xdc000010U));
    native_critical_leave(saved);
}
bool native_usb_ui_active(void)
{
    return input_held || apd_held || busy_held || display_held || cursor_held;
}
static void call(unsigned role)
{
    ((void (*)(void))(uintptr_t)api.function[role])();
    native_critical_enter();
}
static void boolean_call(uint32_t function, unsigned value)
{
    ((void (*)(unsigned))(uintptr_t)function)(value);
    native_critical_enter();
}
static bool memory(uint32_t at, unsigned bytes)
{
    const PortableView *v = portable_reader_platform_view(&mapping);
    return v && !(at & 3) && v->allow_span(v->context, at, bytes, PORTABLE_DATA);
}

static bool timer_valid(uint32_t timer)
{
    return memory(timer, 96) && WORD(timer + 12) == 0x54494d45U &&
           WORD(timer + 24) == api.busy_callback && WORD(timer + 28) == 0 &&
           WORD(timer + 68) == 0 && (WORD(timer + 32) & 255) <= 1 && WORD(timer + 80) <= 1;
}
static bool timer_running(uint32_t timer)
{
    return timer && timer_valid(timer) && (WORD(timer + 32) & 255) == 1 && WORD(timer + 80) == 1;
}
static bool display_disabled(void)
{
    uint32_t s = api.display_state;
    return !WORD(s + 0x58) && !HALF(s + 0x54) && !WORD(s + 0x1c) && !WORD(s + 0x90);
}
static bool display_enabled(void)
{
    uint32_t s = api.display_state;
    return WORD(s + 0x58) == 1 && WORD(s + 0x1c) == 0x54494d45U && (WORD(s + 0x30) & 255) == 1 &&
           WORD(s + 0x28) == api.display_timer && WORD(s + 0x90) == 0x48495352U &&
           WORD(s + 0xc8) == api.display_hisr;
}
static void display_control(unsigned enabled)
{
    /* Its stop routine waits for LCD DMA completion, serviced by the OS IRQ. */
    native_critical_leave(0);
    ((int (*)(unsigned))(uintptr_t)api.function[UI_DISPLAY_CONTROL])(enabled);
    native_critical_enter();
    portable_reader_platform_refresh(&mapping);
}
int native_usb_ui_acquire(bool (*progress)(void *), void *context)
{
    if (native_interrupt_mask() != 0xc0U)
        return -1;
    if (native_usb_ui_active()) {
        if (input_held && apd_held && busy_held && (api.classic_cx || display_held) && cursor_held)
            return native_usb_ui_reassert();
        if (native_usb_ui_release())
            return -19;
    }
    stage = "mapping";
    if (!portable_reader_platform_refresh(&mapping))
        return -2;
    if (!resolved) {
        stage = "OS display interface";
        if (!native_usb_ui_resolve(portable_reader_platform_view(&mapping),
                nspire_asic_is_cx(WORD(0x900a0000U)), &api, progress,
                                   context))
            return -3;
        resolved = true;
    }
    stage = "display state";
    if (api.classic_cx) {
        /* Classic CX renders directly to the LCD buffer. Refuse the alternate
         * native queued-copy mode until its ownership path is validated. */
        if (!memory(api.direct_lcd_flags, 4) || (WORD(api.direct_lcd_flags) & 0x100U)) return -4;
    } else if (!memory(api.display_state, 0xdc + 2048) || WORD(api.display_state) != 1 ||
               WORD(api.display_state + 12) != 153600 || (!display_disabled() && !display_enabled())) {
        return -4;
    }
    stage = "cursor state";
    int count = (int16_t)HALF(api.cursor_count);
    uint32_t timer = WORD(api.busy_base + 4);
    if (count > 0 || count <= INT16_MIN || (timer && !timer_valid(timer)) ||
        WORD(api.busy_base + 12) > 1)
        return -5;
    original_busy = timer_running(timer);
    if (original_busy && (WORD(api.busy_interval) < 10 || WORD(api.busy_interval) > INT32_MAX))
        return -6;
    original_input = HALF(api.event_mask) & 31U;
    original_tracking = HALF(api.tracking_flags) & 8U;
    original_cursor = WORD(0xc0000c00U);
    original_display = api.classic_cx ? 0 : WORD(api.display_state + 0x58);
    HALF(api.event_mask) &= (uint16_t)~31U;
    HALF(api.tracking_flags) &= (uint16_t)~8U;
    input_held = true;
    stage = "automatic power";
    uint32_t power_state;
    original_apd_blocks = apd_blocks(&power_state);
    boolean_call(api.dim_disable, 0);
    boolean_call(api.function[UI_APD_ALLOW], 0);
    apd_held = true;
    stage = "busy cursor";
    busy_held = true;
    if (timer || WORD(api.busy_base + 12))
        call(UI_BUSY_STOP);
    WORD(0xc0000c00U) = original_cursor & ~1U;
    if (WORD(api.busy_base + 4) || WORD(api.busy_base + 12))
        return -7;
    stage = "pointer cursor";
    cursor_held = true;
    call(UI_CURSOR_HIDE);
    if (api.cursor_device_hide) ((int (*)(void))(uintptr_t)api.cursor_device_hide)();
    native_critical_enter();
    if ((int16_t)HALF(api.cursor_count) >= 0 || (WORD(0xc0000c00U) & 1))
        return -8;
    stage = "display copier";
    display_held = !api.classic_cx;
    if (original_display)
        display_control(0);
    if (display_held && !display_disabled())
        return -9;
    stage = "ready";
    return 0;
}
int native_usb_ui_reassert(void)
{
    if (!portable_reader_platform_refresh(&mapping) || !native_usb_ui_active())
        return -10;
    if (input_held) {
        HALF(api.event_mask) &= (uint16_t)~31U;
        HALF(api.tracking_flags) &= (uint16_t)~8U;
    }
    if (busy_held) {
        uint32_t timer = WORD(api.busy_base + 4);
        if (timer && !timer_valid(timer))
            return -11;
        if (timer || WORD(api.busy_base + 12))
            call(UI_BUSY_STOP);
    }
    if (display_held && !display_disabled()) {
        if (!display_enabled())
            return -12;
        display_control(0);
        if (!display_disabled())
            return -13;
    }
    if (cursor_held) {
        if (api.cursor_device_hide) ((int (*)(void))(uintptr_t)api.cursor_device_hide)();
        native_critical_enter();
    }
    WORD(0xc0000c00U) &= ~1U;
    return 0;
}
int native_usb_ui_resume_host(void)
{
    unsigned mask = native_critical_enter();
    int result = -26;
    uint32_t lock = 0, tree_lock = 0;
    bool locked = false, tree_locked = false;
    if (!resolved || !native_usb_ui_active() || !api.bsd_locks || (!api.classic_cx && !api.bsd_mode) ||
        !api.function[UI_SEM_TAKE] || !api.function[UI_SEM_GIVE] ||
        !portable_reader_platform_refresh(&mapping) || !memory(api.bsd_locks, 12)) goto done;
    /* The OS mode is a byte, often at an odd address. Authenticate its backing
     * word rather than asking the word-oriented view to accept a byte pointer. */
    if (!api.classic_cx && (!memory(api.bsd_mode & ~3U, 4) ||
        *(const volatile uint8_t *)(uintptr_t)api.bsd_mode != 1)) goto done;
    lock = WORD(api.bsd_locks + 4); tree_lock = WORD(api.bsd_locks + 8);
    if (lock == tree_lock || !memory(lock, 40) || !memory(tree_lock, 40) ||
        WORD(lock + 12) != 0x53454d41U || WORD(tree_lock + 12) != 0x53454d41U) goto done;
    if (WORD(lock + 24) > 1 || WORD(tree_lock + 24) > 1) goto done;
    /* Try both native locks without sleeping. Holding the tree lock across
     * the driver's timed waits prevents its objects being freed underneath us. */
    result = ((int (*)(uint32_t, unsigned))(uintptr_t)api.function[UI_SEM_TAKE])(lock, 0);
    native_critical_enter();
    if (result) goto done;
    locked = true;
    result = ((int (*)(uint32_t, unsigned))(uintptr_t)api.function[UI_SEM_TAKE])(tree_lock, 0);
    native_critical_enter();
    if (result) goto done;
    tree_locked = true;
    result = -27;
    UsbHostTree tree;
    uint32_t resume = 0;
    if (!portable_reader_platform_refresh(&mapping) || !resume_tree(&tree, &resume)) goto done;
    bool cold = WORD(tree.bus + 604) == 0;
    if (cold) { result = cold_start_check(&tree); if (result) goto done; }
    else if (WORD(tree.bus + 1772) != 1) goto done;
    result = -28;
    UsbRegisters registers;
    if (!usb_registers_for_asic(api.classic_cx ? 0x101U : 0x202U, &registers) ||
        !usb_registers_valid(&registers) || (WORD(registers.otg) & registers.role_mask) ||
        (WORD(registers.command) & (2U | 64U)) ||
        (WORD(registers.command + 4) & 16U)) goto done;
    if (!(WORD(registers.port) & 1U) && !cold) {
        /* An initialized, running host can simply wait for insertion. A cold
         * root-only host below must be started even before PORT_CONNECT:
         * detecting the adapter must not depend on an already running host. */
        if (!WORD(tree.handle + 32) && (WORD(registers.command) & 1U) &&
            !(WORD(registers.command + 4) & 0x1000U)) result = 0;
        goto done;
    }
    /* A live host must not be resumed again while its worker enumerates a
     * new child. Only inspect an empty root or rebind unclaimed storage. */
    if (!cold && !WORD(tree.handle + 32) && (WORD(registers.command) & 1U) &&
        !(WORD(registers.command + 4) & 0x1000U)) {
        result = enumerate_empty_root(&tree, &registers);
        if (!result && portable_reader_platform_refresh(&mapping)) reprobe_storage(&tree);
        goto done;
    }
    result = -29;
    if ((cold || WORD(tree.handle + 32)) &&
        ((WORD(registers.command) & 1U) || !(WORD(registers.command + 4) & 0x1000U) ||
         (!cold && !(WORD(tree.bus + 648) & 1U)))) goto done;
    stage = cold ? "start existing EHCI host" : "resume existing EHCI host";
    if (cold) { ++start_count; start_cleanup_result = 0; }
    native_critical_leave(0);
    if (cold) {
        result = ((int (*)(uint32_t, unsigned, unsigned))(uintptr_t)
            api.function[UI_HCD_CONTROL])(tree.hcd, 18, 0);
    } else {
        result = ((int (*)(uint32_t))(uintptr_t)resume)(tree.hcd);
    }
    native_critical_enter();
    if (!result) {
        if (!portable_reader_platform_refresh(&mapping) || !resume_tree(&tree, &resume) ||
            WORD(tree.bus + 1772) != 1 || !WORD(tree.bus + 604) ||
            (cold && WORD(tree.hub_state + 36) != 1) ||
            !(WORD(registers.command) & 1U) || (WORD(registers.command + 4) & (0x1000U | 16U))) result = -30;
        else {
            result = enumerate_empty_root(&tree, &registers);
            if (!result && portable_reader_platform_refresh(&mapping)) reprobe_storage(&tree);
        }
    }
    if (cold) {
        start_result = result;
        if (result) {
            /* Registration may have succeeded before the native start failed.
             * Let its matching stop method release the interrupt and halt the
             * schedules. Never leave a half-started controller after failure. */
            UsbHostTree current;
            if (portable_reader_platform_refresh(&mapping) && host_tree(&current) &&
                current.hcd == tree.hcd && WORD(current.bus + 604)) {
                native_critical_leave(0);
                start_cleanup_result = ((int (*)(uint32_t, unsigned, unsigned))(uintptr_t)
                    api.function[UI_HCD_CONTROL])(current.hcd, 19, 0);
                native_critical_enter();
            }
        }
    }
done:
    if (tree_locked) {
        ((int (*)(uint32_t))(uintptr_t)api.function[UI_SEM_GIVE])(tree_lock);
        native_critical_enter();
    }
    if (locked) {
        ((int (*)(uint32_t))(uintptr_t)api.function[UI_SEM_GIVE])(lock);
        native_critical_enter();
    }
    native_critical_leave(mask);
    return result;
}

int native_usb_ui_poll(void)
{
    unsigned mask = native_critical_enter();
    if (!native_usb_ui_active() || !memory(api.task_global, 4)) {
        native_critical_leave(mask);
        return -22;
    }
    uint32_t task = WORD(api.task_global);
    if (!memory(task, 0x44) || WORD(task + 0x0c) != 0x5441534bU || WORD(task + 0x38)) {
        native_critical_leave(mask);
        return -22;
    }
    /* Observation of an old attachment must not suspend the caller on an OS
     * timer/event that may no longer be serviced. Keep this span bounded;
     * ordinary new-device enumeration retains the scheduler wait below. */
    native_critical_leave(0);
    for (volatile unsigned spins = 0; spins < 8192U; ++spins) { }
    native_critical_leave(mask);
    return 0;
}

int native_usb_ui_wait(void)
{
    stage = "USB scheduler wait";
    unsigned mask = native_critical_enter();
    if (!native_usb_ui_active() || !memory(api.task_global, 4)) {
        native_critical_leave(mask);
        return -22;
    }
    uint32_t task = WORD(api.task_global);
    if (!memory(task, 0x44) || WORD(task + 0x0c) != 0x5441534bU || WORD(task + 0x38)) {
        native_critical_leave(mask);
        return -22;
    }
    /* IRQs alone do not let lower-priority USB tasks run. The app must leave
     * the runnable queue briefly, as the probe's native dialog does. */
    native_critical_leave(0);
    ((void (*)(unsigned))(uintptr_t)api.function[UI_TASK_SLEEP])(1);
    native_critical_enter();
    ++scheduler_waits;
    stage = "ready";
    native_critical_leave(mask);
    return 0;
}

bool native_usb_ui_clock(const volatile uint32_t **counter, uint32_t *hz,
                         bool (*progress)(void *), void *context)
{
    if (!counter || !hz)
        return false;
    *counter = NULL;
    *hz = 0;
    unsigned mask = native_critical_enter();
    /* Native dialogs need the OS clock before display/input ownership is taken.
     * Resolving the interface has no ownership side effects. */
    if (!resolved) {
        if (!portable_reader_platform_refresh(&mapping) ||
            !native_usb_ui_resolve(portable_reader_platform_view(&mapping),
                nspire_asic_is_cx(WORD(0x900a0000U)), &api, progress, context)) {
            native_critical_leave(mask);
            return false;
        }
        resolved = true;
    }
    bool valid = memory(api.clock_counter, 4) && memory(api.clock_hz, 4);
    if (valid) {
        uint32_t rate = WORD(api.clock_hz);
        valid = rate && rate <= 10000U;
        if (valid) {
            *counter = (const volatile uint32_t *)(uintptr_t)api.clock_counter;
            *hz = rate;
        }
    }
    native_critical_leave(mask);
    return valid;
}

int native_usb_ui_release(void)
{
    if (!native_usb_ui_active())
        return 0;
    if (!portable_reader_platform_refresh(&mapping))
        return -14;
    stage = "restore display";
    if (display_held) {
        if (original_display && display_disabled())
            display_control(1);
        if (original_display ? !display_enabled() : !display_disabled())
            return -15;
        display_held = false;
    }
    stage = "restore cursor";
    if (cursor_held) {
        call(UI_CURSOR_SHOW);
        cursor_held = false;
    }
    if (busy_held) {
        uint32_t timer = WORD(api.busy_base + 4);
        if (timer && !timer_valid(timer))
            return -16;
        if (original_busy) {
            if (!timer)
                call(UI_BUSY_START);
            if (!timer_running(WORD(api.busy_base + 4)))
                return -17;
        } else if (timer) {
            call(UI_BUSY_STOP);
            if (WORD(api.busy_base + 4))
                return -18;
        }
        WORD(0xc0000c00U) = original_cursor;
        busy_held = false;
    }
    if (apd_held) {
        boolean_call(api.function[UI_APD_ALLOW], 1);
        apd_held = false;
    }
    if (input_held) {
        HALF(api.event_mask) = (HALF(api.event_mask) & (uint16_t)~31U) | original_input;
        HALF(api.tracking_flags) = (HALF(api.tracking_flags) & (uint16_t)~8U) | original_tracking;
        input_held = false;
    }
    stage = "idle";
    return 0;
}
