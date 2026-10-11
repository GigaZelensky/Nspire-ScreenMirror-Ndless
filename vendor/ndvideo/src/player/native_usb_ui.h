#ifndef NDVIDEO_NATIVE_USB_UI_H
#define NDVIDEO_NATIVE_USB_UI_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
/* Call with genuine native SRAM and IRQ/FIQ masked. The reservation remains
 * across connection windows so the OS cannot redraw over the player. */
int native_usb_ui_acquire(bool (*)(void *), void *);
int native_usb_ui_reassert(void);
/* One OS tick with the native mapping and Nucleus interrupt policy enabled. */
int native_usb_ui_wait(void);
/* Bounded IRQ span; no OS sleep/yield call. */
int native_usb_ui_poll(void);
/* Resume a suspended bus, or start a verified CX II root-only bus whose
 * interrupt is not registered. Never recreate the whole host. */
int native_usb_ui_resume_host(void);
/* Resolve without taking UI ownership. Progress keeps the connection window
 * responsive before the OS timer is handed back for native calls. */
bool native_usb_ui_clock(const volatile uint32_t **counter, uint32_t *hz,
                         bool (*progress)(void *), void *context);
int native_usb_ui_release(void);
bool native_usb_ui_active(void);
const char *native_usb_ui_stage(void);
void native_usb_ui_debug(FILE *);
#endif
