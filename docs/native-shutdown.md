# Native USB shutdown investigation

The experimental SRAM receiver remained on its closing screen for several minutes,
including after the cable was unplugged. The OS still dimmed the display and handled
brightness shortcuts. The user reset the calculator. This is an exit failure; those
observations do not establish that overclocking caused it.

## Firmware findings

The CX II OS 6.2 dump used for this investigation has SHA-256
`24ed6a134e6458fdb03523b3aa049a5558b5cd33f409a30609d363f557b3b5e3`.
IDAPython exports are kept locally under `build/usb-stop-analysis`.

- The public `TI_NN_Read` entry at `0x100bb17c` replaces timeouts from 1 through
  61,999 with 62,000, then tail-calls the channel read at `0x100cc6d4`. Our old
  750 ms request therefore did not specify the short wait intended by the app.
- The channel implementation accepts a shorter timeout directly and drops its
  read reference before returning. A timeout becomes a successful API return
  with no output bytes. Sending a BAD acknowledgement in that case would create
  an unsolicited reply, so the receiver instead checks exit and keeps waiting.
- `TI_NN_SS_StopService` acquires service/channel mutexes and deletes channel
  resources. Resource deletion at `0x100cf0a0` wakes read/write events and loops
  until the resource reference count at offset 24 reaches zero. That loop has no
  deadline. A stuck reference or mutex can therefore strand the native call.
  We did not capture the calculator's live lock/reference state during the hang.

## Receiver changes awaiting hardware verification

The Ndless syscall table supplies the read entry address; the receiver does not
assume a model-specific address. It bypasses the timeout clamp only after matching
the wrapper, both timeout literals, its tail branch, and the full 280-byte channel
read code witness. External BL destinations and PC-relative literal positions are
normalized for firmware relocation. On an unrecognized firmware it retains the
public API and explicitly logs `public_timeout_unverified`; it does not claim a
bounded timeout in that case.

The recognized path requests 100 ms. Empty reads neither consume frames nor send
unsolicited replies. Packet/reply storage moves out of the serialized callback's
stack into app-owned static buffers. The compiled callback's own stack frame falls
from approximately 2 KiB to 296 bytes; this is not proof that a stack overflow
caused the earlier hang. Further tracing of `sub_100D2770` and its pthread
attribute helpers confirms a 64 KiB service worker stack on this firmware, so
the callback's former stack size alone is unlikely to explain the failure.

Exit refuses new callbacks, waits for the existing callback, yields twice to let
the native dispatch wrapper release references, and restores the entry clock
before `StopService`. It still requires successful native service removal before
unloading the application. It does not abandon a registered callback or pretend
that a hung native operation succeeded.

Diagnostics now write at startup/exit, on Ctrl+D, and on decode failure, including
the input FNV hash. Before native shutdown they record the current clock, callback
count, read entry/target, and shutdown stage. The remaining physical test must
confirm that the read witness matches and that exit succeeds under streaming,
idle, disconnected, and repeated Start/Stop conditions.

## Clock comparison

Two approximately four-minute 396 MHz USB runs completed without decoder/USB
errors. A subsequent 492 MHz run with SRAM loans disabled requested a fresh
keyframe at captured frame 1498 after about 51 seconds. Its 599-frame GOP passed
FFmpeg, production C, and production ARM decoder replay under QEMU. QEMU does not
model the calculator's IRQ, cache, bus, or overclock behavior. The calculator's
input hash from that run was lost in the exit hang, so byte corruption on the
device versus decoder execution remains unresolved.

`tools/benchmarks/replay_usb.py` replays the same captured bytes at 396 and 492 MHz,
alternates clock order between rounds, disables experimental SRAM loans when
available, and records confirmed clock, progress, failures, and receiver profiles.
This removes changing encoder output/content from the next clock comparison.

## Physical close-fix test

Seven 599-frame passes completed on the calculator: three at 396 MHz and four
at 492 MHz. The same captured bytes averaged 27.33 and 29.80 FPS respectively,
with a 30 FPS sender cap. No quality setting changed. The eighth pass failed
during the USB opening handshake before sending any frames; its intended
396 MHz clock was not applied, and it is not a decoder test failure.

The receiver log confirms `path=verified_body`, entry `100bb17c`, target
`100cc6d4`, and 100 ms timeout. All 4,193 frames used direct VRAM. There were no
decoder failures. Exit from the paused screen completed promptly; the log shows
`service_stopped`, zero callbacks, successful native stop result 1, and the
entry 396 MHz clock restored before stopping USB. A subsequent exit during
active 492 MHz playback also returned promptly, and the PC received the
explicit receiver-closing acknowledgement.

These short runs do not rule out rare overclock faults. The remaining handshake
failure and exit after physically unplugging USB need separate checks. The
write API's nominal 62-second argument reaches different transports: the WS_IO
path delegates directly to TI_CN_Write and does not consume that timeout.
Changing the public write timeout literal would therefore not bound all writes.

The next, longer 492 MHz replay failed at displayed stream frame 2346 after
1,446 submitted frames (about 49 seconds). This corresponds to captured frame
1148 in the third repetition. Its 2,307-byte input hash `5d490cd9` matches the
PC capture. The calculator reported `slice-resume`, bit 15852, from resume
MB 224, skip 3, QP 20, input bit 13622, already-decoded flag 0, and matching
input pointer. All those saved fields match the successful C replay at the
same point; that replay advances through MB 256 and input bit 17749 instead
of failing. FFmpeg and production C both decode all three repetitions
(1,797 frames). Matching the pre-decode hash does not test later input reads
or the rest of the decoder's working state. The fault remains unresolved.

Exit after this failure was prompt. Its log again confirms successful native
service removal, zero callbacks, and restoration to 396 MHz. USB error -2
was recorded after the benchmark stopped on the decoder's reset request;
it does not establish that USB input corruption caused the decode error.

## Callback syscall gateway

Ndless `resources/ints.c` keeps `saved_spsr`/`saved_lr` and one nested pair in
shared storage. These are not task-local. A blocking callback syscall can
overlap foreground syscalls; if the tasks return out of nesting order, the
gateway can select another task's saved return state. This is a concurrency
risk, not proof of the observed slice error.

The next receiver resolves Read, Write and Current_Task once from the installed
Ndless syscall table before registering its service, validates their mapped
OS code addresses, and calls them directly in the callback. Read retains the
verified timeout-body resolver; unknown read code retains the public function
and logs its timeout as unverified. Read/Write explicitly enable IRQs for
their native waits and restore the incoming mask. Queue operations remain
under IRQ/FIQ exclusion. The callback's short memory helpers execute under
that exclusion; the blocking network waits no longer hold shared gateway
return slots. Foreground Start/Stop still use the normal gateway with no live
callback blocking call. Physical validation of this candidate is pending.

The direct-export callback candidate completed 10,782 frames across two long
396 MHz passes and one long 492 MHz pass, with no decoder errors. One additional
492 MHz pass failed at USB opening before its clock or frames were submitted.
The subsequent physical unplug test submitted 354 frames; the receiver decoded
355 frames including the final packet whose PC acknowledgement was interrupted.
The calculator stayed in callback-drain shutdown for approximately one to two
minutes, then returned normally. Its final log confirms the direct gateway,
100 ms read target, 11,137 direct frames, zero decoder failures, and clock restore.
The unplug exit delay therefore remains unresolved.

## Withdrawn short-write copy

A recognized 236-byte native writer plus its literal pool was copied into
app-owned RAM with absolute-call veneers and a 250 ms deadline literal. ARM926
emulation with native-call stubs confirmed its calling convention, packet
arguments, error mapping and deallocation paths. That did not cover the real
driver's pending-packet lifetime or disconnect handling.

On hardware this candidate played 1,507 frames, then the calculator rebooted
immediately when USB was physically unplugged, before Ctrl+Esc. The experiment
has been withdrawn. Its implementation, fixture and checks are retained only
under ignored `build/retired-short-write`; its calculator binary is renamed
`ndmirror-WITHDRAWN.tns.disabled`. Normal source uses the original native Write
export. Do not present the shorter deadline copy as supported or fixed.

The rollback receiver retains the verified read timeout, direct callback API
calls and exit-clock restoration. It adds read/write phase and longest-duration
logging without altering the writer. The next investigation must find a proper
native cancellation/completion operation instead of assuming a smaller timeout
is sufficient. The crash cannot currently be assigned to overclocking versus
the experimental cancellation/lifetime change.

## Rollback connected check and completion path

The original-writer rollback receiver (`9e640d94c451a4aa997e41081c9d2847f3ac1978d802c3dad5386f27ab1d7902`)
completed the same 599-frame capture at 396 MHz without a reported failure.
The user then confirmed prompt Ctrl+Esc exit from its waiting screen with USB
still connected. This verifies the rollback's connected playback and idle exit;
it does not verify unplug handling. The receiver timing log was retrieved
directly over USB from `/videoplayer/ndmirror.log.tns`; its SHA-256 is
`8a7384ed6a080b2e92c9d5294240c27b6711af9a90458c3a26efee4d7b653ec2`.
It confirms zero USB/decoder errors, 599 direct frames, service stop result 1,
zero callbacks, restored 396 MHz, maximum read 60 ms and maximum write 40 ms.

Further ARM call tracing identifies `0x100DA4FC` as
`TI_NS_mark_packet_complete` from its firmware log strings. It takes the pending
packet's mutex, changes its state to complete, frees and clears the packet,
records its result, signals the completion event if a waiter is registered,
clears the waiter flag, and releases the mutex. Merely signalling that event
would skip the packet ownership changes.

`0x100DA90C` is `TI_NS_fail_all_io`. It locks the connection's held-transmit
queue, removes pending entries, and calls that completion operation with error
-26 and wake enabled. The native connection teardown at `0x100D64A0` invokes it
before waking/stopping the receive worker and destroying connection resources.
The NOAPP response handler at `0x100DA5D0` uses the same queue lock and completion
operation for a matching packet, with error -27.

These are native completion paths worth investigating, not callable app APIs
established by this analysis. The connection lifetime, outer locks and selected
USB transport must be resolved before an app can invoke one safely. None is
currently called or patched by the receiver.

## Suspended-worker diagnostic candidate

The packet-completion findings above concern the legacy NI path. The CX II
WS_IO path delegates to a connector's Write method without consuming the
channel writer's timeout argument. Its stream removal marks the node stopped,
stops the listener, waits for its transmit mutex and frees the connection.
Calling legacy `TI_NS_fail_all_io` is not established as cancellation for that
connector. The live blocked call must be identified before selecting a fix.

The next candidate preserves the original writer and adds read-only suspended
worker capture. `TCT_Check_Stack` validates the OS 6.2 task's bounds at +0x24 and
+0x28; `TCT_Build_Task_Stack` aligns the end and stores saved SP at +0x2C, and
the context switch uses that SP. Capture requires mapped RAM, TASK at +0x0C,
aligned valid stack bounds/SP, and individually readable words. It copies at
most 128 words without changing native state and retains the longest captured
wait in the final log. A single `usb_drain_wait` write after one second of exit
preserves evidence while a blocked callback is still active. Physical testing
is pending. The candidate uses direct VRAM with SRAM disabled.

The candidate (`608f94e21df78a8ecff21321256dfc0638d9bf760090fbead44b986e521152a0`)
was transferred to `/videoplayer/ndmirror.tns` through the standalone file tool
and its read-back matched exactly. The previous rollback receiver was retrieved
first and preserved in `outputs/debugfunnies/receiver-backups` outside Git.
Receiver launch and unplug behavior remain pending physical validation.

## Captured default-clock unplug wait

The diagnostic receiver completed 674 frames at 396 MHz before the physical
unplug. Exit again took roughly one to two minutes. Its retrieved log
(`d043ec6a9b7aa73601deac67fdc261651693db0b947575a25e6de274a09155c6`)
records maximum native Write duration 60020 ms, no decoder failures, successful
service stop, and a suspended Write snapshot at 6110 ms. This reproduces the
exit delay without overclocking.

The saved stack includes return PCs from channel Write through WS_IO_send_packets,
WS_IO_send_single_packet, the CX II connector Write at 0x10616314, packet send
at 0x106194D4, NNSE send at 0x10618430, and event retrieval at 0x1061A014.
The waiting event group is 0x11424300; the request waits for ACK/disconnect bits
5. The legacy NI packet-cancellation path is not the active wait in this run.
Code-looking stack words are not all return addresses: 0x106178B8, for example,
is a packet method pointer and was rejected as a caller after raw disassembly.

The native connector state-change handler at 0x10616AA8, with connected=0, marks
the connector disconnected, resets the receive queue, invokes NNSE cleanup at
0x1061A254, updates connector availability and publishes the disconnected state.
NNSE cleanup resets its send queue, signals disconnect bits 12 while event-group
waiters remain, yields between attempts and clears the event group afterward.
Calling the NNSE helper alone would omit the disconnect flag needed by the native
receive listener's error path.

The next candidate invokes that full native handler during app shutdown only
when an outstanding Write and a debounced physical B-device session end agree.
It matches both complete code/literal witnesses and requires the tested ASIC,
export addresses and WS mode. Unknown firmware retains the original operation
and logs unsupported cancellation explicitly. No native code or packet fields
are patched. Session definitions are cross-checked against
[U-Boot's FOTG210 register header](https://android.googlesource.com/platform/external/u-boot/+/refs/heads/android-tv-9.0.0_r1/include/usb/fotg210.h).
It requires a readable capability register, B-device/device role, session end,
and no valid B session. Logging can yield, so the active Write and physical
session are rechecked before calling. The handler runs with IRQ/FIQ enabled
and restores the incoming mask. Native service removal still waits for callback
and wrapper completion; the candidate does not abandon an active task.

Candidate SHA-256 is
`b145886cacfe7fc86ca7cd9d384fc51688c1f72014782d8c3ed0cde75e0c5b36`.
It was transferred and read back byte-for-byte through the standalone tool.
The previous diagnostic receiver was preserved before replacement. Physical
verification of this candidate is pending.

The first candidate's unplug test exited promptly but its log records
`supported=0`, `attempts=0`, maximum Write 40 ms and no blocked snapshot.
This did not exercise cancellation and is not evidence that the fix worked.
The code lookup had incorrectly required an active WS mode before StartService
could initialize that mode. The revised candidate checks code independently and
checks the live transport mode only before invocation. Its connected idle-exit
log confirms `supported=1`, target 0x10616AA8 and both matching code hashes.

The revised receiver is `dbfcdbf30c2c421af95997f6b815efae625e9b62f9a3184061f11d076e6e6ced`.
The verified idle log is `cbaeefedf2b4d3b212287878cd8a2ec8abb0a82253a1a2f6295711b6baf1d2be`.
Its physical cancellation test is still pending.

`tools/benchmarks/held_write.py` deliberately withholds an NNSE acknowledgement
for a valid Mirror HELLO reply, leaving that writer pending before requesting
physical unplugging and Ctrl+Esc. This avoids relying on whether a video happens
to be reading or writing at the instant of unplugging. It caps withheld messages
and releases the ACK after 40 seconds if the user does not unplug. The receiver
stays on its waiting screen during this test. The initial 40-second window
expired before a physical unplug was observed; its 39160 ms write and zero
cleanup calls do not verify cancellation. The probe now drains duplicate replies
and renews the held write every 45 seconds for up to five minutes, ending early
on physical unplug or an explicit stop file.

The renewed physical-unplug run held 24 acknowledgements and detected device
removal. The receiver returned promptly and the log records supported cleanup
but zero calls. Its 27890 ms write ended with native disconnect error -259.
The OS handled that idle pending-write case before our handler was needed.

## Direct handler verification and normal-build restore

A temporary `--test-native-disconnect` receiver bypassed only the physical-session
gate during app shutdown, allowing the same verified handler to be exercised
with USB still connected and an acknowledgement deliberately held. Its log
`11aa2360a0ec0e7cfd8f2b6aa7b14af6f389fead950c3afd74cd0b08b7ac355a`
records supported=1, attempts=1, result=0, force_test=1, live WS mode 1, both
matching code hashes, zero callbacks and native service stop result 1. The user
confirmed prompt exit. Subsequent direct USB file retrieval worked without a
required physical replug. Thus the handler itself was physically exercised,
not merely enabled or bypassed.

The normal build was rebuilt with the test override disabled:
`204e05872199d1ce1ee7f7f5bd1582d088311c2416a4d12211c36c1948cbd939`.
It retains the physical-session requirement and has been restored to the
calculator. Direct VRAM remains enabled and SRAM loans remain disabled. A video
unplug/exit run on this exact normal binary remains to be checked; the earlier
clean idle runs must not be described as exercising the guarded handler.

## Connected restart/removal hang

A later Stop, codec change and Start failed during USB discovery. The receiver
log `9e4a7d4dec502e5aada3c844526e2d5f424959a73275a089cd3fd162ab83c84c`
ends at `before_native_service_stop`, with callbacks=0, max Write=50 ms and no
decoder failures. The app entered the native service-removal call and did not
return before the user reset it. Callback completion and two task yields did
not establish that the native wrapper had released every resource.

The next receiver completes the same fully witnessed native connector handler
before service removal during explicit shutdown. It accepts a live B-device
session as well as an ended one; it does not wait for physical unplugging. It
still validates firmware/exports/controller role, enables IRQs for the handler,
retains its debounce/recheck and waits for successful service removal before
unloading. The earlier connected held-write run exercised this handler, but this
new ordinary-exit sequence needs its own physical testing.

The PC side allocates fresh native endpoints rather than reusing 0x8000 on each
connection. A consumed helper error clears the pending RPC slot; a timeout does
not. Stop confirms the PAUSE acknowledgement's need-key state. A confirmed PAUSE
already returns the application callback to the OS wrapper's channel deletion,
so the host pumps final traffic briefly rather than issuing a competing ECLOSE.
These changes address concrete lifecycle hazards; they do not establish which
individual hazard caused the captured native hang.

The normal receiver `70059349f5aad1635dfee59f98a69c725d8d8ef6505dfb0c84d6045547ceb82c`
passed 12 alternating H.264/MPEG-4 Start/Stop cycles at 492 MHz with generated
colour patterns. Its display counters recorded 251 frames, with no decoder
failures. The user then exited with USB connected; exit was prompt and the OS
file service immediately returned the log without unplugging.

That log, `2af356e8c4ffdb3197a93de133b4db08f89bb8eba354ca335863511b2da4e2ff`,
records `service_stopped`, callbacks=0, stop_result=1, native cleanup attempts=1,
result=0 and force_test=0. The controller role, both native code hashes and live
WS mode matched. The entry clock was restored to 396 MHz. This exercises the
ordinary connected-exit path; the short restart sequence does not establish
long-run reliability or validate other calculator/OS combinations.

Normal desktop use subsequently reproduced a reboot after codec switching, so
the short test was insufficient. The host never received that run's HELLO
reply. A longer generated-pattern comparison (medium, automatic quality,
controls enabled, four seconds per run) passed eight entry-clock cycles, then
failed on the sixth 492 MHz cycle. This failure occurred while reopening the
physical USB interface between INPUT and STREAM, before decoding any frame.
Ctrl+D/Ctrl+Esc still exited promptly; log
`b4b9f5fd99e8ff5bdd4d87322c60752deabc6bb22cb308ebe1718b137e0be98a`
shows service_stopped and WS mode=0, without requiring native abort.

The helper previously recreated its USB handle and restarted NNSE address/time
initialization for every logical setup channel. It now retains the same handle,
receive state and NNSE sequence through setup/playback, and initializes once per
helper connection. Its normal close releases the physical handle. This revised
host passed 24 alternating four-second runs at 492 MHz with the same medium/
automatic/control settings, using the unchanged receiver. This removes repeated
physical reinitialization from the failing path; a single comparison does not
prove that overclocking itself caused the user's reboot.

The user also confirmed normal live-video codec switching with the rebuilt host.
This supersedes the earlier short-test-only result, while remaining limited to
the hardware and duration exercised. The host no longer attempts stream PAUSE
cleanup if opening the stream channel itself failed.
