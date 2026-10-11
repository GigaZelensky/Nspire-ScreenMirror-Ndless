"""Receiver snapshots retain stage history and preserve the last manual event."""

from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="mirror-log-check-") as directory:
    directory = Path(directory)
    source = directory / "check.c"
    source.write_text(
        r"""
#include "diagnostic_log.h"
#include <assert.h>
#include <string.h>
int main(void) {
    bool enabled = false;
    assert(!mirror_log_enabled(&enabled, "service_starting"));
    assert(!mirror_log_enabled(&enabled, "decode_failure"));
    assert(!mirror_log_enabled(&enabled, "exit_requested"));
    assert(mirror_log_enabled(&enabled, "manual_save"));
    assert(mirror_log_enabled(&enabled, "service_stopped"));
    MirrorLogHistory history = {0};
    for (unsigned n = 0; n < 40; ++n)
        mirror_log_record(&history, (MirrorLogEntry){"fixture", 1700000000U+n,
            100U+n, 73, n+1, n, 492, 0, 0, 0});
    assert(history.count == 16);
    FILE *file = fopen("main.log", "wb");
    assert(file);
    mirror_log_print(file, &history);
    assert(!fclose(file));
    assert(mirror_log_copy("main.log", "event.log"));
    file = fopen("main.log", "wb");
    assert(file); fputs("closing checkpoint", file); fclose(file);
    file = fopen("event.log", "rb");
    assert(file);
    char line[300];
    unsigned n = 0;
    while (fgets(line, sizeof(line), file)) {
        unsigned long rtc, ticks, session;
        assert(sscanf(line, "diagnostic_history rtc=%lu ticks=%lu session=%lu",
                      &rtc, &ticks, &session) == 3);
        assert(rtc == 1700000024U+n && ticks == 124U+n && session == 73);
        assert(strstr(line, "MHz=492"));
        ++n;
    }
    fclose(file);
    assert(n == 16 && !mirror_log_copy("absent.log", "event.log"));
    file = fopen("event.log", "rb");
    assert(file && fgets(line, sizeof(line), file));
    assert(strstr(line, "rtc=1700000024"));
    fclose(file);
    return 0;
}
"""
    )
    binary = directory / "check.exe"
    subprocess.run(
        [
            shutil.which("gcc"),
            "-std=c99",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I" + str(ROOT / "calc"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], cwd=directory, check=True)
main = (ROOT / "calc/main.c").read_text()
assert "ndmirror-event.log.tns" in main
assert '"manual_save"' in main and '"decode_failure"' in main
assert "mirror_log_record(&log_history" in main
assert "diagnostic_identity rtc=%lu ticks=%lu session=%lu" in main
assert "receiver=diagnostic.receiver(state)" in (ROOT / "pc/mirror.py").read_text()
print("PASS bounded ordered receiver history, RTC/session identity and independent event snapshot")
