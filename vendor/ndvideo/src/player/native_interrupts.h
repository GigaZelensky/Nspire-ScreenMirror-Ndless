#ifndef NDVIDEO_NATIVE_INTERRUPTS_H
#define NDVIDEO_NATIVE_INTERRUPTS_H
/* Raw CPU mask helpers. No OS service, scheduler or timer access. */
unsigned native_interrupt_mask(void);
unsigned native_critical_enter(void);
void native_critical_leave(unsigned saved_mask);
#endif
