#include "nspire_hardware.h"
#include <libndls.h>
#include <os.h>

static uint32_t hardware_id;
static unsigned sdk_subtype_at_entry;
static bool initialized;

void nspire_hardware_init(void)
{
    if (initialized) return;
    uint32_t id = *(const volatile uint32_t *)0x900a0000U;
    if (id == 0x202U || nspire_asic_is_cx(id)) hardware_id = id;
    sdk_subtype_at_entry = nl_hwsubtype();
    initialized = true;
}
uint32_t nspire_hardware_id(void)
{
    if (!initialized) nspire_hardware_init();
    return hardware_id;
}
unsigned nspire_initial_sdk_subtype(void)
{
    if (!initialized) nspire_hardware_init();
    return sdk_subtype_at_entry;
}
bool nspire_is_cx2(void)
{
    return nspire_hardware_id() == 0x202U;
}
bool nspire_on_key_pressed(void)
{
    uint32_t id = nspire_hardware_id();
    if (id == 0x202U)
        return (*(const volatile uint32_t *)0x90140810U & 0x100U) == 0;
    if (nspire_asic_is_cx(id))
        return (*(const volatile uint32_t *)0x900b0028U & 16U) == 0;
    return on_key_pressed() != 0;
}
