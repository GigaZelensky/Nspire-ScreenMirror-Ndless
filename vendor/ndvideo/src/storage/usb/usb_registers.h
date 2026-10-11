#ifndef NDVIDEO_USB_REGISTERS_H
#define NDVIDEO_USB_REGISTERS_H
#include "../../platform/nspire_hardware.h"

/* Logical offsets used by the transport are the CX II layout. Original CX
 * has a TDI EHCI core at +0x100, with a 0x40-byte capability block and the
 * standard port offset. Keep that translation outside the transfer engine. */
typedef struct {
    uint32_t capability, command, port, otg, otg_mask, role_mask;
    bool classic;
} UsbRegisters;

static inline bool usb_registers_for_asic(uint32_t asic, UsbRegisters *r)
{
    if (nspire_asic_is_cx(asic)) {
        *r = (UsbRegisters){0xb0000100U, 0xb0000140U, 0xb0000184U,
                           0xb00001a4U, 0xb00001a4U, 1U << 8, true};
        return true;
    }
    if (asic == 0x202U) {
        *r = (UsbRegisters){0xb0000000U, 0xb0000010U, 0xb0000030U,
                           0xb0000080U, 0xb00000c4U, 1U << 21, false};
        return true;
    }
    *r = (UsbRegisters){0};
    return false;
}

static inline uint32_t usb_register_address(const UsbRegisters *r, unsigned logical)
{
    return logical == 0x30U ? r->port : r->command + logical - 0x10U;
}

static inline bool usb_registers_current(UsbRegisters *r)
{
    return usb_registers_for_asic(*(const volatile uint32_t *)0x900a0000U, r);
}

static inline bool usb_registers_valid(const UsbRegisters *r)
{
    uint32_t cap = *(const volatile uint32_t *)(uintptr_t)r->capability;
    return (cap & 0xffff00ffU) == (r->classic ? 0x01000040U : 0x01000010U);
}

/* Adapter ID is a PHY/OTG signal, not an EHCI port-status signal. It can
 * remain readable while the host is stopped or its capability block sleeps.
 * Zero/ones from a gated controller are unknown, not proof of removal. */
static inline bool usb_registers_id_readable(const UsbRegisters *r, uint32_t cap,
                                            uint32_t identity, uint32_t otg)
{
    if (otg == UINT32_MAX) return false;
    if ((cap & 0xffff00ffU) == (r->classic ? 0x01000040U : 0x01000010U))
        return true;
    if (r->classic)
        return (identity & 0xffffU) == 0xfa05U;
    return cap == 0 && otg != 0;
}
#endif
