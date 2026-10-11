# SRAM and display RAM

CX II has 256 KiB of internal SRAM and 153,600 bytes of LCD memory at A8000000. The latter holds exactly one 320×240 RGB565 frame. [Hackspire](https://www.hackspire.org/Memory-mapped_IO_ports_on_CX_II/) describes its fixed address transformation for the LCD's layout. This is a display aperture, not a programmable video rotation/scaling engine.

The existing apps already output to that LCD memory on the tested CX II. Earlier player display logs recorded `target=a8000000`. Screen Mirror calls Ndless `lcd_blit`; the player's landscape path uses its optimized framebuffer copy. Ndless supplies layout conversion where required. Neither app needs a second rotation just because the hardware has this memory.

The supplied memory charts show why moving arbitrary working data to VRAM is not automatically useful: its streamed writes are much slower than DRAM/SRAM, even though reads can be fast. The useful experiment is converting final pixels directly into the existing VRAM aperture, avoiding the intermediate RGB framebuffer copy. This can increase visible tearing or make overlays expensive, so actual conversion times and the displayed result both matter.

The player already owns a guarded SRAM arena. Its CX II path relocates native SRAM state/page tables into a DRAM clone and uses the physical high 128 KiB for player data. It restores genuine native SRAM for operations that require it. Screen Mirror continuously services native USB and currently uses the original mappings. Copying the player's arena setup into it would require proving that CPU callbacks, interrupt stacks, page-table updates and USB DMA still agree about the memory they own.

SRAM is a candidate for frequently accessed decoder/reference data, rather than the final screen. A 320×240 YUV420 picture is 115,200 bytes; that alone nearly fills a 128 KiB arena. Tiny tables may already fit the CPU cache, so SRAM placement is not a guaranteed gain. A zero-filled region or a faster read benchmark does not establish that the OS has released that region for an app to overwrite.

## Standalone benchmark

```sh
python build.py --sdk /path/to/ndless-sdk --main-source tools/benchmarks/memory_display.c --output build/memory-display-probe
```

The output is a separate executable despite the builder's `ndmirror.tns` basename; rename it to `ndmemory.tns` when copying it. It does not replace the normal receiver. Enter, 5 or the touchpad centre starts four rounds; Esc exits. The log is `ndmemory.log.tns` beside the executable.

It compares the same ARM YUV420 conversion through native buffered blit, buffered burst copy and direct VRAM conversion. Direct modes run only on a CX II whose native RGB565 landscape framebuffer is exactly A8000000. Otherwise the log records an explicit unsupported-layout result. Display RAM is written, never read back. The test pattern must look identical in all modes, including its right edge; timing does not establish visual correctness or suitability during moving video.

Read timings compare allocated DRAM with the existing native SRAM mapping, at 8 KiB and 128 KiB working sets. Each read measurement runs for at least a quarter second so the native OS tick can resolve it. Native timer interrupts remain active. SRAM is read only: no reservation, remapping, cache-policy changes or clocks are performed. These results describe current mappings, not all cache-policy combinations in the supplied charts. They do not prove SRAM can safely be borrowed during live USB streaming.

The four-round CX II result at the default 396 MHz clock was 10.703 ms/frame for native buffered conversion/blit, 10.781 ms for buffered burst copy, and 7.539 ms for direct conversion. This saves 3.164 ms (29.6% of this rendering stage), not 29.6% of total video decoding. The test's three patterns were reported identical. Native SRAM word reads were 19 ns versus 51 ns in DRAM for the 128 KiB working set; at 8 KiB they were 17 ns versus 18 ns. These are this scalar read workload and native cache mappings, not a decoder benchmark.

An experimental mirror receiver can be built with `build.py --direct-vram`. It converts H.264 pictures directly into the verified uncached CX II landscape aperture when the stats overlay is hidden. Other models/layouts, overlays and stateful lossless updates retain the buffered path. Calculator logs record the build mode and actual direct/buffered frame counts, so an inactive path cannot be mistaken for an acceleration. No display DMA is involved. This remains opt-in until moving video, overlay toggles and USB lifecycle tests pass.

The player branch is unchanged by this investigation. Its future-frame cache contains RGB pictures and the renderer composites scaling, subtitles, night effects and controls through a RAM surface. Simply replacing that surface with VRAM would expose partially composed/future frames and invalidate screenshots. The mirror's measured direct YUV conversion benefit cannot be applied to cached RGB pictures without changing that pipeline. SRAM integration likewise requires proving ownership during native USB callbacks; the read-only measurement does not reserve an arena.
