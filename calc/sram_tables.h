#ifndef MIRROR_SRAM_TABLES_H
#define MIRROR_SRAM_TABLES_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#ifndef MIRROR_SRAM_TABLES
#define MIRROR_SRAM_TABLES 0
#endif
#if MIRROR_SRAM_TABLES
#define MIRROR_PROFILE_WORDS 80U
void mirror_tables_init(uint32_t os_hz);
void mirror_tables_enable(bool enabled);
const int32_t *mirror_tables_begin(const int32_t *original);
void mirror_tables_sample_begin(void);
void mirror_tables_sample_end(void);
bool mirror_tables_end(void);
void mirror_tables_words(uint32_t words[32]);
void mirror_tables_debug(FILE *file);
#else
#define MIRROR_PROFILE_WORDS 48U
#endif
#endif
