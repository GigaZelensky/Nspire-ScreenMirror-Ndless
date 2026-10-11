#ifndef MIRROR_DIAGNOSTIC_LOG_H
#define MIRROR_DIAGNOSTIC_LOG_H
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
#include <string.h>

#ifndef MIRROR_DIAGNOSTIC_AUTOSAVE
#define MIRROR_DIAGNOSTIC_AUTOSAVE 0
#endif

static bool mirror_log_enabled(bool *enabled, const char *stage) {
    if (!strcmp(stage, "manual_save"))
        *enabled = true;
    return *enabled;
}

#define MIRROR_LOG_HISTORY 16U
typedef struct {
    const char *stage;
    uint32_t rtc, ticks, session, received, displayed, mhz;
    unsigned callbacks, io;
    int result;
} MirrorLogEntry;
typedef struct {
    MirrorLogEntry entries[MIRROR_LOG_HISTORY];
    unsigned count, next;
} MirrorLogHistory;

static void mirror_log_record(MirrorLogHistory *history, MirrorLogEntry entry) {
    history->entries[history->next] = entry;
    history->next = (history->next + 1U) % MIRROR_LOG_HISTORY;
    if (history->count < MIRROR_LOG_HISTORY)
        ++history->count;
}
static void mirror_log_print(FILE *file, const MirrorLogHistory *history) {
    for (unsigned n = 0; n < history->count; ++n) {
        unsigned index = (history->next + MIRROR_LOG_HISTORY - history->count + n) %
                         MIRROR_LOG_HISTORY;
        const MirrorLogEntry *entry = &history->entries[index];
        fprintf(file,
                "diagnostic_history rtc=%lu ticks=%lu session=%lu stage=%s result=%d "
                "received=%lu displayed=%lu MHz=%lu callbacks=%u io=%u\n",
                (unsigned long)entry->rtc, (unsigned long)entry->ticks,
                (unsigned long)entry->session, entry->stage, entry->result,
                (unsigned long)entry->received, (unsigned long)entry->displayed,
                (unsigned long)entry->mhz, entry->callbacks, entry->io);
    }
}
static bool mirror_log_copy(const char *source, const char *destination) {
    FILE *input = fopen(source, "rb");
    if (!input)
        return false;
    FILE *output = fopen(destination, "wb");
    if (!output) {
        fclose(input);
        return false;
    }
    unsigned char buffer[512];
    size_t bytes;
    bool ok = true;
    while ((bytes = fread(buffer, 1, sizeof(buffer), input)) != 0) {
        if (fwrite(buffer, 1, bytes, output) != bytes) {
            ok = false;
            break;
        }
    }
    if (ferror(input) || ferror(output))
        ok = false;
    fclose(input);
    if (fclose(output))
        ok = false;
    return ok;
}
#endif
