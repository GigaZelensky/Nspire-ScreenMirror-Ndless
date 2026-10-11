# Live USB colour-table comparison

Build the experimental receiver with:

```sh
python build.py --sdk /path/to/ndless-sdk --direct-vram --sram-tables --output build/receiver-sram-test
```

This is a CX II-only experiment. It retains the original MMU mappings and native
USB service. Only H.264's final colour conversion can borrow the 5,504 bytes at
A4020000. The gate checks the native mapping, banked stacks, USB host/DMA activity
and the recognized FTDMAC020 channels before every loan. Busy or unsupported
frames use RAM and increment a reason counter.

The original bytes are backed up with IRQ/FIQ masked. No native calls or yields
occur until conversion is finished and those bytes have been restored and
checked. If verification fails, the loan is disabled and the failure is recorded;
if a second restoration cannot be verified, execution stays masked with a red
screen rather than returning to the OS with altered SRAM. No cache ways are
locked and no MMU, DMA, clock or timer registers are changed by the loan.

Use the matching PC sender with the following diagnostic options:

```sh
ND-Screen-Mirror.exe --start --clock 396 --profile --sram-ab-seconds 30 --sram-ab-phases 8 --capture-failure-stream --frame-metrics timings.jsonl
```

Leave a video or game moving on the mirrored display, with calculator stats off
to keep direct VRAM output active. RAM and SRAM phases alternate for four pairs;
the sender drains the last frame before changing mode. It stops after about four
minutes. Exit the receiver with Ctrl+Esc to save `ndmirror.log.tns` beside it.
The test build always requests an exit log.

Receiver profiles include SRAM/RAM frame counts, skip reasons, restoration errors
and separate conversion/setup/restore/verification timer totals. Host frame
metrics include capture, encode, transfer, credit waiting, adaptive bitrate and
displayed-frame counts. The nominal native timer can be coarse; phase means are
more useful than individual strip timings. Compare deltas between phase
boundaries, including loan overhead, and confirm that SRAM frames actually ran.
Adaptive quality can also change load, so record bitrate and frame sizes along
with FPS rather than claiming a quality-independent speedup.

Connection failures and optionally their encoded sequences are captured by the
sender. A reconnect restarts the phase sequence; counters on the receiver remain
cumulative until it exits. Neither this build nor its timing result enables SRAM
loans in the normal release.
