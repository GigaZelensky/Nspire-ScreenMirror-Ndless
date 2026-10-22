# Validation

Windows 64-bit and CX II have shown H.264/MPEG-4 streaming, calculator pointer movement and centre click. Original CX and Linux/Pi remain untested on hardware.

The restart13 receiver SHA-256 is `58c8d3f0877c0875bf06e2e90307e4f3fc9abec5d02697cd9b8abe4862dd9fdf`. At 396 MHz, the tester confirmed Stop/Start returned the picture and Ctrl+Esc returned to the calculator OS. Two measured sessions averaged 29.28 and 29.95 displayed FPS with a 640×480 virtual desktop and 320×240 receiver. This describes that test workload, not every game or clock.

The tester also confirmed that moving the virtual monitor in Windows kept the picture working and that Stop/Start preserved placement, using the layout14 PC build and the same receiver.

The signed virtual driver has been installed and checked. Native 320×240, 480×360, 640×480, 1280×960 and custom 720×405/512×384 modes were attached/captured/detached while physical display resolutions/positions stayed unchanged.

## Performance observations

At 492 MHz, a controlled moving-video sample reached roughly 35 FPS uncapped with the persistent channel; a simpler pattern reached 48 FPS. A recorded gameplay replay improved from about 14 FPS at fixed fast/CRF10 to 29.7 FPS with adaptive ultrafast, trading detail for frame rate. Replay excludes live capture work and is not a universal FPS guarantee.

Measured application payload throughput at 396 MHz was approximately 0.57–0.58 MiB/s. Raw 30 FPS needs 4.6 MB/s before overhead. Timing ending at acknowledgement is not full display latency.

On a static desktop at 492 MHz, extending H.264 keyframe spacing from five to 30 seconds increased mean displayed FPS from 29.36 to 29.91 over separate 35-second runs. The 95th-percentile acknowledgement interval fell from 44.68 to 37.03 ms. A 930-frame offline test also crossed the longer reference chain and next IDR through the production C decoder, matching FFmpeg.

Live video runs averaged 28.38–29.33 displayed FPS; the latest recovery-controller run averaged 28.80 over 40 seconds and the tester confirmed the image looked good. These were different playing scenes, not a controlled quality comparison. Host capture averaged about 5 ms and encoding below 1 ms. Most variation was in RPC/receiver waiting; it cannot be attributed to USB bandwidth alone. Receiver stage timings from a run reporting bad clock samples were excluded. Sanitized host evidence is in `docs/validation/timings.json`.

## October 13 clock and reconnect tests

The timing-retention receiver (`854a232b736d7564339dbe2cc2294cc36e23982583de7da7aeb255a726d4ffe0`)
completed five minutes at 480 MHz: 9,725 frames, approximately 32.4 displayed FPS,
no decoder failures or timing mismatches, and no reported stray pixels. Two longer
playback/switch runs totalled approximately 30 minutes and 60,000 frames without
decoder failures. These used fixed CRF14/ultrafast or MPEG-4 Q1, not automatic quality.

492 MHz remained unreliable on this calculator. One live run failed after 31 seconds
despite the requested memory timing being retained. The saved calculator input
differed from the PC stream at one byte; substituting that byte reproduced the same
decoder error on the host. This does not isolate the CPU, memory or transport as its
physical cause. Lowering the clock is appropriate when a unit shows these symptoms.

Repeated physical USB initialization also timed out without video frames. Retaining
one initialized Windows helper passed 100 fresh application channels and 20 rapid
H.264/MPEG-4 switches at 480 MHz, averaging 32.6 displayed FPS at fixed quality.
The tester confirmed prompt receiver exit while the stopped sender retained its
handle; the final log showed no decoder failures and correct clock/SRAM restoration.
The desktop sender now retains only a successfully paused/retired link. This avoids
that repeated-initialization path; recovery from every physical-link failure is not
established.

## Open validation

October 22 adaptation work remains a candidate. In one continuous low-motion
desktop comparison, settled windows averaged 28.97 displayed FPS with the old
controller and 29.92 with the candidate. Encoder targets ranged from
519–1549 kbps and 852–947 kbps respectively. This comparison preceded the
subsequent high-resolution clock fix and is not evidence for every source.

A later three-minute 480 MHz live run completed 5,329 submitted frames without
a logged USB failure and saved its final 829-frame GOP. FFmpeg and the production
C receiver both decoded all 829 frames. Repeating those identical bytes four
times per clock on the public receiver completed 3,316 frames at 396 MHz
(141.63 seconds, 23.41 FPS) and 3,316 at 480 MHz (110.71 seconds, 29.95 FPS),
with no reported replay failure. Internal receiver stage timings were excluded
because their clock-sample checks failed.

A separate 480 MHz live run froze after 2,492 acknowledged frames, about
84 seconds. Its exact failing GOP was not captured. The successful replay
above therefore does not reproduce or resolve that freeze. The buffered
display/automatic-log probes were withdrawn after startup/shutdown hangs;
their changes are not in the current candidate. The original public quiet
receiver remains the hardware baseline.

A further comparison re-encoded the same 829-frame RGB sequence using the
original and candidate controllers, both at 480 MHz with the same precise
pacer and two-minute duration. After a 15-second warmup, displayed FPS averaged
29.25 versus 29.80; rolling samples below 29 FPS fell from 31/103 to 6/104.
Payload averaged 771 versus 699 kbps. Comparing 808 matching source frames in
their final GOPs gave RGB PSNR 46.06 versus 45.73 dB: a small objective fidelity
tradeoff, not evidence of equal perceptual quality. This test excluded GDI
capture work and remote input. A subsequent three-minute candidate pass with
remote input enabled completed 5,351 frames without a streaming error,
averaging 29.76 FPS after warmup. These checks did not reproduce the earlier
live-desktop freeze.

A later normal desktop run rebooted the calculator after about 17 minutes
at 480 MHz. The last acknowledgement accepted frame 31,082 and displayed
31,081 with no rejected frames or keyframe request. The host preserved the
last 483 encoded frames, including the first unacknowledged frame, 31,083.
FFmpeg and the production C decoder decoded all 483 frames on the host.
Replaying those exact bytes twice per clock on the unchanged public receiver
completed 966 frames at 396 MHz and another 966 at 480 MHz without a replay
failure. No new calculator crash report survived the reboot. These results
do not resolve a longer-session, ARM-specific, native USB or clock-related
failure; the candidate is not yet established as reliable for long live runs.
An isolated 396 MHz check then completed 66,000 ordinary status requests,
crossing the host's natural 16-bit outer-packet counter wrap without an error.
This exercised the basic rollover path without video decoding; it does not
exclude a failure that requires concurrent decoding or overclocking.

- Broader keyboard/layout/input combinations and different TI installations.
- Broader clock/hardware combinations and physical USB error recovery. The measured 492 MHz failures above remain a limitation.
- Original CX and Linux/Pi hardware.
- Final binaries after rebuilding. Earlier receiver results do not automatically apply to another hash.

Sanitized evidence is in `docs/validation`. Original recordings and transient logs remain local and are excluded from releases.
