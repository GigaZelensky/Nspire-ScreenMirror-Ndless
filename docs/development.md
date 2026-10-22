# Development

The repository includes selected decoder/platform sources under `vendor/ndvideo` and the calculator-specific nSDL subset under `vendor/nsdl`. Builds do not read a neighbouring player checkout. Provenance and per-file hashes are recorded in each vendor directory. Preserve notices and document local vendor changes.

## Dependencies

Use Python 3.11+, ARM GNU `arm-none-eabi-gcc`/`ar`, and the Ndless SDK. MinGW `gcc`/`windres` build the Windows display installer.

```sh
python -m pip install -r requirements-dev.txt
```

Set `NDLESS_SDK` to `ndless-sdk` or pass `--sdk PATH`. No personal checkout paths are built into the scripts.

`make` builds the receiver, plus the PC sender on Windows. `make check`, `make lint`, `make pc` and `make package` provide the common build targets. `SDKROOT=/path/to/ndless-sdk` is also accepted by Make.

## Calculator receiver

If the SDK includes nSDL headers/library:

```sh
python build.py --sdk /path/to/ndless-sdk --output build/calculator
```

Otherwise build the included nSDL source without modifying the SDK:

```sh
python build_sdl.py --sdk /path/to/ndless-sdk
python build.py --sdk /path/to/ndless-sdk --sdl-directory build/nsdl --output build/calculator
```

Outputs are `ndmirror.tns`, ELF and Zehn files. SDK support libraries and the loader come from the SDK. Compilation preserves ARM926 software-float, LTO and the optimized decoder conversion routines. A rebuilt receiver is a new binary: earlier hardware evidence does not automatically apply to its hash.

Direct VRAM output is enabled for the verified CX II mapping. Stats overlays,
other picture modes and other models use the buffered path. `--no-direct-vram`
selects buffered output for comparisons. SRAM table borrowing stays off unless
an experimental receiver is explicitly built with `--sram-tables`.

`--test-native-disconnect` is a hardware-test override. It may disconnect the
native USB link on exit while the cable remains connected, and must never be
used for release builds. Normal builds require a validated device-role link and
perform native connector cleanup only during explicit receiver shutdown, including
when the cable remains connected.

## Windows sender

```sh
python build_display_setup.py
python tools/build_pc.py
```

The generated display helper is ignored by Git. Building/verifying it does not install a driver. Actual setup validates the pinned INF/DLL/catalog and Windows signature, and updates only the app's managed device/configuration. It requires elevation without changing certificates or driver-signing policy.

The packaged sender is a windowed executable. Use `python pc/mirror.py --cli` for an interactive console; developer metrics can also be enabled on the executable with `--metrics` or `--frame-metrics`.

## USB and display setup notes

Windows uses the included Python protocol implementation and Microsoft's inbox WinUSB API; CX II requires no TI installation. Linux uses the same protocol through libusb. See [USB protocol and driver notes](usb-protocol.md) and [Linux setup](linux.md). The old NavNet and libnspire transports are explicit developer options (`--backend navnet` / `--backend libnspire`); neither is selected automatically.

The display helper verifies its signed driver inputs and changes only the app's managed display. New custom resolutions may require elevation. It does not change global Windows scaling or driver-signing settings. Windows scaling cannot go below 100%; increase the virtual desktop resolution or use an app's zoom instead.

GUI preferences are in `%LOCALAPPDATA%/ND Screen Mirror/settings.json` on Windows and `$XDG_CONFIG_HOME/ND Screen Mirror/settings.json` (default `~/.config`) on Linux. Explicit launch arguments override saved values; console runs do not load GUI preferences. H.264 CRF and MPEG-4 Q use different scales. Lossless/raw modes are diagnostic alternatives for testing bandwidth and image detail. Windows input follows the active keyboard layout and cannot inject into a more privileged app or a secure desktop. Linux input uses evdev/uinput with selectable US/French letter positions, relative mouse events and high-resolution scrolling. Its host-layout selector is a physical letter mapping; it does not use a text-injection API or query the active compositor layout.

Wayland capture keeps a ScreenCast portal session alive, subscribes before requesting consent, and passes its PipeWire descriptor to GStreamer. Scaling precedes RGB conversion. The frame reader stores only the newest complete 320×240 frame and reuses it on unchanged desktops. Stop cancels pending approval, terminates capture and closes the session and descriptor. Sharing denial is reported; there is no automatic XWayland capture fallback.

## Checks

```sh
python tools/run_checks.py --sdk /path/to/ndless-sdk
python -m ruff check pc checks tools build.py build_sdl.py build_display_setup.py package.py
python -m ruff format --check pc checks tools build.py build_sdl.py build_display_setup.py package.py
```

The normal runner uses fake USB/display/input APIs where appropriate. It does not open calculator USB or send real keyboard input. It compares actual H.264/MPEG-4 C decoders against FFmpeg and covers fragment/queue bounds, input ordering, adaptive bitrate, shutdown and the GUI desktop lifecycle. `--interactive-input` additionally targets its own Windows entry; inability to obtain foreground is recorded as skipped, not passing.

Hardware benchmarks under `tools/benchmarks` are opt-in. Some capture screen content or control an attached receiver. Their recordings and outputs remain outside Git and release archives.

## Packaging

```sh
python package.py dist/ND-Screen-Mirror-0.1.0-windows --calculator-build build/calculator --pc-build build/pc/ND-Screen-Mirror --check-report build/checks/report.json
```

Use a fresh destination. Packaging refuses existing files rather than mixing old releases. It includes sender, receiver, complete project/vendor sources, licenses, dependency versions, source commit, receiver hash and SHA256SUMS. Developer recordings and old logs are excluded.

Optional `--hardware-validation FILE` must contain the actual receiver SHA. A mismatch fails packaging. Without matching evidence, physical validation is false. Offline tests never imply hardware validation; source dirtiness is recorded explicitly.

Verify ZIP CRC/checksums, run the extracted package, and test paired receiver/sender on the target hardware before publication. This local preparation does not publish anything.

## GitHub Actions

The build workflow follows the player project's push, pull request, release and manual-build conventions. Ubuntu builds a pinned Ndless toolchain/SDK and the receiver; Windows runs the full offline suite and packages the sender. SDK headers/libraries used by the Windows checks come from the same Ubuntu build. The SDK revision is in `.github/ndless-revision`; only the ARM toolchain installation is cached.

Default-branch pushes update the `latest` development release. Version tags must match `VERSION` with a `v` prefix. A manual `release_tag` builds an existing tag; an empty manual input only creates build artifacts. Publishing has a separate write-permission job and never runs for pull requests. Existing releases are updated without deleting the release. Action revisions are pinned, and original CI artifacts include check reports and build hashes. Automated builds never imply a hardware test.

The hosted workflow has completed successful calculator, Windows and publishing runs. Each source update still needs its own successful run; local checks do not establish the state of the hosted build.

## Streaming and lifecycle

Service 0x4008 carries bounded NDM1 messages. CX II negotiates 1440-byte messages; classic CX uses 254. Two receive slots bound memory. H.264 uses Baseline/CAVLC, one reference, no B frames/lookahead or deblocking, repeated headers and a 30-second GOP. `--keyframe-seconds` adjusts that interval. MPEG-4 uses five seconds. Reconnection starts a fresh encoder and IDR.

The persistent channel shares frame acknowledgement, credit and input events. PAUSE ends it on Stop; a pending frame RPC is drained before its terminal reply. Receiver CLOSING ends PC retries and releases input. Receive callbacks do queue work; foreground code decodes and presents. Exit drains callbacks before stopping the native service, with native interrupts enabled for teardown. Live callback pointers are never deliberately left in an unloaded program.

The GUI owns its virtual monitor while open. Stop releases stream/capture resources while preserving desktop windows. Close removes it. Capture reads the current Windows coordinates and updates the same region used by pointer control. Owned-device identity, requested mode and existing monitor layout are checked.

The native GUI is isolated in `pc/desktop.py`. The executable manifest declares per-monitor DPI awareness; source runs set it before Tk creates its first window. Detailed per-frame timing logs remain opt-in.

PC diagnostic logging is disabled by default. Enable **Save diagnostic logs** in Advanced or pass `--diagnostic-log`; explicit developer metrics/capture flags also enable it. Enabled runs keep a rotating connection log at `%LOCALAPPDATA%/ND Screen Mirror/logs/connection.log` (Linux: `$XDG_STATE_HOME/ND Screen Mirror/logs/connection.log`, default `~/.local/state`). It records stream settings, connection transitions and failures with the previous 16 frame sizes/receiver acknowledgements. Every five seconds it also records aggregate capture, encoding, USB, credit, pacing and timer-wake costs with displayed FPS, the active MPEG-4 Q, and receiver frame counters. It does not write per-frame timing records, screen content or input events. Logs rotate at 512 KiB with three backups. A failure to create/write the log is reported to the sender's status callback. Offline stream tests do not enable the per-user log.

The receiver keeps the last 16 stage checkpoints and decoder-error details in bounded RAM. It writes no diagnostic files by default. Ctrl+D enables file logging for the current session and saves `ndmirror.log.tns` plus an independent `ndmirror-event.log.tns` snapshot beside the receiver. Later failures and shutdown are recorded while enabled; shutdown does not overwrite the event snapshot. `--diagnostic-logging` builds a receiver with automatic checkpoints enabled, and the explicit input-capture/guard builds enable those files too.

MPEG-4 automatic quality uses the existing motion/timing feedback with an inverse-Q budget in the range Q1–Q8. Those internal budget units are not a bitrate setting. Live Q changes retain the encoder and reference chain, and the initial matrix tables include Q1 even when starting at another Q. PyAV's `global_quality` already converts Q to FFmpeg lambda units; the application does not multiply by 118 again. Fast enables rate-distortion block decisions; medium also enables trellis. These settings keep the existing chroma format, one reference and no B frames.

H.264 adaptation combines displayed progress, queue occupancy and unpaced frame
cost. A bounded byte/cost fit estimates a capacity only when sample variation and
correlation support it; otherwise the controller uses measured headroom and small
additive probes. Reductions retain an overload ceiling for the motion band,
discard stale feedback and allow the new rate to settle. Application-limited
scenes do not chase unused bitrate. Low FPS alone does not justify an emergency
cut when frame work is cheap and the receiver queue has room. These follow
[AIMD capacity/probing principles](https://webrtc.googlesource.com/src/+/main/modules/remote_bitrate_estimator/aimd_rate_control.cc),
adapted to this receiver's CPU and USB constraints rather than a network-only model.

Frame scheduling, cost measurements and latest-capture timestamps share
`time.perf_counter()`. Python 3.12 on Windows uses a coarse `GetTickCount64`
clock for `time.monotonic()`; mixing it with precise sleeps still creates
frame-sized timing error. The pacing helper uses high-resolution Python sleeps
in at most 8 ms slices for cancellation/input handling, without a busy loop.
See the [CPython clock clarification](https://github.com/python/cpython/issues/126745).

The receiver advertises display-rate synchronization and accepts a bounded tenths-of-FPS value once per second. Both displays then use the same displayed-frame IDs and PC monotonic time. Receiver OS ticks can undercount elapsed time while a long decode masks interrupts; they are no longer used for its FPS overlay. Legacy receivers can still stream, but the PC log records that their overlay needs an update.

The opt-in developer flag `--capture-failure-stream` also retains the current H.264 GOP in PC RAM, bounded to 16 MiB/1800 frames. On a failure it saves the encoded screen content and frame metadata under the log directory's `failure-streams` folder, retaining three captures. It is disabled by default. Those captures allow replaying the exact data through FFmpeg and the production decoder; they are local diagnostic data, excluded from Git and packages. Normal Stop/receiver exit does not create a capture.

The receiver build option `--decode-guard` is a diagnostic experiment, disabled in normal builds. At pending H.264 steps it checks the already-unescaped input, decoder/storage structures, macroblock storage/layer, slice map and active parameter-set structures before and after native scheduling/display reassertion. Comparison uses saved allocation addresses, avoiding changed pointers. An unexpected change preserves `ndmirror-event.log.tns` and closes the receiver without resuming or freeing the possibly damaged decoder. The log's region numbers follow that order (0–7). These checks add processing cost and cover selected allocations, not every reference-picture pixel or external CPU state. They are not a playback optimization or proof that all decoder state is intact.

`--capture-decode-input` keeps an independent original-input copy in RAM before decoding, without the expensive yield checks. `--decode-guard` enables this copy too. On a decoder failure only, it saves those bytes as `ndmirror-frame.bin.tns` beside the receiver and records the frame/session/hash in the event log, for byte comparison with the PC capture. It also records the current decoder-buffer hash before filesystem calls (normal NAL unescaping may alter that buffer), plus CX II PLL/EMI readbacks immediately after clock selection and before decoding. This adds 64 KiB of RAM and saves encoded screen content locally; normal builds have neither the copy nor its file output.

Verified CX II idle-timing retention is enabled in normal builds; `--no-idle-timing` disables it for developer comparisons. It validates the ROM idle routine, registered SRAM entry, uncached writable identity mapping, and complete 344-byte native SRAM image. For an explicitly selected overclock only, it changes the idle-restore literal502 to528, preserving the native instructions, low-clock/WFI literal and power-policy state. Default-clock selection and exit restore502 and verify the whole original image. Native image reinitialization is recognized; an unrelated code change is preserved and reported. The snapshot build counts before-decode timing matches/mismatches for every H.264 frame in the current clock phase. This keeps the requested memory timing through native idle; it does not establish that higher CPU clocks are stable. A 492 MHz live run still produced confirmed input corruption despite correct timing. The earlier shared idle-policy experiment stays in private investigation artifacts, with an unresolved asynchronous restoration corner; it is not shipped.

GUI preferences use a versioned per-user JSON file and atomic replacement. Only validated setting values are stored; sessions, library paths, automatic startup and diagnostic output paths are excluded. GUI edits are saved after a short debounce and on close, while explicit launch arguments take precedence. Offline GUI tests use isolated temporary preference files.

Adaptive H.264 uses live x264 CBR/VBV reconfiguration without filler, from 125 kbps to 2 Mbps. Thumbnail motion prediction complements recent receiver progress. Capture stalls are distinguished from decode/transfer pressure. No per-frame flash writes or extra software CRC occur; native USB integrity checks remain.

CLI `--pipeline` and `--profile` are development experiments. Pipelined capture has not consistently improved latency. Profile samples with clock inconsistencies do not establish valid stage timings. Capture-to-ack ends at acknowledgement, not panel display.

`--frame-metrics PATH` records buffered per-frame host timings without saving screen pixels or input events. It includes capture, encoding, RPC transfer, credit wait, pacing and acknowledgement intervals. RPC time includes receiver waiting; it is not a measurement of USB bandwidth alone. `tools/benchmarks/bench_live.py --owned-display` follows the currently attached calculator display without acquiring or detaching the GUI's display lease. Keep the GUI sender stopped during this recording.
