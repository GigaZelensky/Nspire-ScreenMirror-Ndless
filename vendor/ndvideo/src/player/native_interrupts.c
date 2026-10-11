#include "native_interrupts.h"

static void compiler_barrier(void)
{
    __asm__ volatile("" : : : "memory");
}

unsigned native_interrupt_mask(void)
{
#if defined(__arm__)
    unsigned cpsr;
    __asm__ volatile("mrs %0, cpsr" : "=r"(cpsr));
    return cpsr & 0xC0U;
#else
    return 255U;
#endif
}

unsigned native_critical_enter(void)
{
#if defined(__arm__)
    unsigned saved, masked;
    __asm__ volatile("mrs %0, cpsr\n\torr %1, %0, #0xc0\n\tmsr cpsr_c, %1"
                     : "=&r"(saved), "=&r"(masked)
                     :
                     : "memory", "cc");
#else
    unsigned saved = 255U;
#endif
    compiler_barrier();
    return saved & 0xC0U;
}

void native_critical_leave(unsigned saved_mask)
{
    compiler_barrier();
#if defined(__arm__)
    unsigned current, bits;
    __asm__ volatile(
        "mrs %0, cpsr\n\tbic %0, %0, #0xc0\n\tand %1, %2, #0xc0\n\torr %0, %0, %1\n\tmsr cpsr_c, %0"
        : "=&r"(current), "=&r"(bits)
        : "r"(saved_mask)
        : "memory", "cc");
#else
    (void)saved_mask;
#endif
    compiler_barrier();
}
