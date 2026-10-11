# Colour-table experiment

`tools/benchmarks/cache_tables.c` builds a separate calculator app. It uses the
mirror's ARM YUV420-to-RGB565 converter and the same 5,504 bytes of colour tables.
It does not start the mirror service or stream USB data.

```sh
python build.py --sdk /path/to/ndless-sdk --main-source tools/benchmarks/cache_tables.c --output build/cache-tables-probe
```

Rename the output `ndmirror.tns` to `ndcache.tns` when copying it. Exit the mirror,
unplug USB and launch this app at the default calculator clock for the first run.
Enter, 5 or the touchpad centre runs four rounds. Esc cancels or exits.
`ndcache.log.tns` is saved beside the app.

The cases are ordinary RAM, warmed RAM, RAM with the Y and clipping tables pinned
in one cache way, RAM with all colour tables pinned in three ways, and native
SRAM table placement. Their order rotates between rounds. Each case tests RAM
and direct VRAM output, with and without a competing 64 KiB read workload.
Conversion, competing reads, setup and restoration have separate timing totals.
Divide each total by `frames` to get microseconds per frame. RAM output is checked
against a reference outside the timed conversion; VRAM is not read back.

Cache locking uses the ARM926's way-allocation register. Tables are aligned to
2 KiB; the selective fill routine makes no stack or literal-pool data accesses.
Hot-table pinning leaves three ways available; full pinning leaves one. The
experiment requires an enabled 8 KiB, four-way, 32-byte-line data cache, cached
table mappings and no pre-existing OS lockdown. It checks the written lockdown
mask and restores the previous allocation mask before calling native code.
The procedure follows the [ARM926 manual](https://documentation-service.arm.com/static/5e8e3d1088295d1e18d3a9b2).

SRAM placement temporarily borrows the 5,504-byte range starting at A4020000.
It requires a validated CX II, its native page table, the recognized uncached
SRAM mapping, banked stacks outside that range plus an 8 KiB margin, stopped USB
host engines, no active USB device DMA, and idle channels on the recognized
FTDMAC020. It backs up the range for each conversion, masks IRQ/FIQ, copies in
the tables, converts, restores the original bytes and drains buffered writes.
It makes no native calls while the range is overwritten and changes no MMU,
clock, timer or DMA registers. Unsupported cases are explicitly marked as
skipped in the log and on the final screen.

Timing reads the existing native periodic timer without changing it. Eight-row
conversion strips and 4 KiB read blocks are sampled individually, so intervals
are shorter than a timer reload even with IRQs masked. These totals measure the
converter and read workload, not complete H.264 decoding or mirroring FPS.

A successful isolated SRAM result does not establish ownership during live USB
callbacks. Cache pinning also needs a complete decoder benchmark: reserving most
of the data cache can make the rest of the decoder slower. Neither technique is
enabled in the released mirror by this experiment.
