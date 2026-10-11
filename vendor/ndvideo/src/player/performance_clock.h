#ifndef NDVIDEO_PERFORMANCE_CLOCK_H
#define NDVIDEO_PERFORMANCE_CLOCK_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

/* Startup/exit and native-standby boundaries only, with original OS SRAM
 * mapped and app I/O drained. Never call from a playback frame or writer. */
bool performance_clock_start(void);
/* Two startup boundaries retain the entry settings and reconcile after native
 * display/font/theme setup. They do not run from playback or background I/O. */
void performance_clock_startup_begin(void);
bool performance_clock_startup_complete(void);
bool performance_clock_restore(void);
bool performance_clock_finish(bool normal_exit);
void performance_clock_debug(FILE *file);
void performance_clock_load(unsigned mhz, bool keep_after_exit);
unsigned performance_clock_selection(void);
bool performance_clock_keep_after_exit(void);
unsigned performance_clock_current_mhz(void);
unsigned performance_clock_default_mhz(void);
unsigned performance_clock_max_mhz(void);
unsigned performance_clock_option_count(void);
unsigned performance_clock_option(unsigned index);
bool performance_clock_valid_mhz(unsigned mhz);
bool performance_clock_choose(unsigned mhz, bool keep_after_exit);
bool performance_clock_test(unsigned mhz, uint32_t *before, uint32_t *after);
int performance_clock_status(void);
#endif
