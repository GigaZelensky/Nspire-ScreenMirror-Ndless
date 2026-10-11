# USB transport

Windows connects through its built-in `winusb.dll` and `winusb.sys`. No TI application, NavNet DLL, bridge server, libusb DLL or Python USB package is required by the default Windows backend. The calculator receiver is unchanged.

The CX II advertises `USB\\MS_COMP_WINUSB` and a device-interface GUID in its Microsoft OS descriptors. Windows 10/11 can install the inbox driver from these descriptors. Discovery enumerates present device interfaces with SetupAPI, including GUIDs read from the device's registry properties. It only opens TI vendor 0451, products E022 (CX II) and E012 (CX). It never changes a driver or writes to the registry.

Original CX transport is implemented for a WinUSB-bound interface but remains untested. Its older TI driver is not WinUSB; a PC with that binding needs a compatible USB driver before the direct backend can open it. There is no automatic driver replacement or silent fallback to TI libraries.

## Protocol references

The packet format, checksum algorithms and baseline address/time handling were reimplemented using [Vogtinator/libnspire](https://github.com/Vogtinator/libnspire), commit `7d7d962ca49ff9017f6f146336c51ef7f2edf4e0`. Original authors include Daniel Tang and Felix Böttcher. `pc/nspire_usb.py` is distributed under GPL-3.0-or-later; the project's GPLv3 license is included.

The Windows CX II startup sequence was verified by observing WinUSB calls made by the locally installed TI software. This added two details beyond that reference: send NNSE address-service payload `01` to restart discovery without unplugging; use native destination `c801` for application packets (host `6400`). The address response is `80`; the unknown-service request `01` receives `81 03`; a time response completes initialization. Those observations contain protocol bytes, not copied TI binary code. TI binaries and the research tools are not bundled.

CX II NNSE messages have a 12-byte big-endian header, one's-complement checksum, and sequence-matched acknowledgements. Stream service 4 carries native Nspire packets. Their 16-byte header identifies addresses and service ports, with a header byte sum and data checksum. Larger payloads use size FF plus a four-byte length. Screen Mirror uses service 4008, up to 1440 bytes per message on CX II or 254 on CX.

Incoming application messages are queued while waiting for an outgoing packet acknowledgement; they are never discarded merely because they arrived first. Transfer waits and receive queues are bounded. All calls run in the existing disposable USB helper so Stop can cancel a stalled device without blocking the GUI. New channels use fresh native host endpoints. After a confirmed PAUSE, the receiver's native dispatch wrapper deletes its channel; the sender briefly acknowledges final traffic instead of racing that deletion with another ECLOSE. Control channels still disconnect explicitly. The next Start requests a fresh native handshake.

The helper retains one physical USB handle and NNSE sequence across its logical
channels. Address/time initialization runs once when that handle opens, rather
than between HELLO, CAPS, CLOCK, INPUT and STREAM. Logical channel end releases
the native endpoint; helper shutdown releases the physical handle. A transport
error during channel teardown discards the handle instead of reusing uncertain
link state.

The Windows desktop app retains that initialized helper after a clean Stop.
It confirms Pause, closes the native application channel, and opens a fresh
channel on the next Start. This avoids repeating address/time discovery when
switching codecs. A failed transfer or unconfirmed channel close discards the
helper; closing the desktop app releases it. Close the PC app completely before
using a separate calculator file-transfer tool.

The output pipe enables WinUSB `SHORT_PACKET_TERMINATE`: transfers whose size is an exact multiple of the endpoint's packet size must end with a zero-length USB packet. Without it, a 480-byte application message becomes a 512-byte CX II transfer and stalls the calculator's receiver. Hardware checks reproduced this timeout with termination disabled and verified 512/1024-byte transfers with it enabled. The application bytes and protocol checksums are unchanged.

Linux uses this same packet implementation with libusb bulk endpoints. It claims only the TI interface, restores a detached kernel driver when releasing it, and sends a zero-length packet after exact endpoint-sized writes. USB permissions are configured by the rules in `tools/70-ndmirror.rules`; the app does not run as root.

For comparisons only, `--backend navnet` explicitly selects the old TI backend and requires a local TI installation. Normal Windows runs never select it, search for TI DLLs or launch a bridge process. `--backend libnspire` selects the earlier Linux library adapter explicitly.

## Retrieving calculator logs

`tools/calculator_files.py` is a separate Windows tool for the OS file
service (`4060`). Exit the calculator receiver and stop the PC sender first;
TI linking software must also be closed. No calculator app replacement is needed.

```powershell
python tools/calculator_files.py find-log --output C:\path\to\logs
python tools/calculator_files.py get /videoplayer/ndmirror.log.tns --output C:\path\to\logs
python tools/calculator_files.py put build\ndmirror.tns /videoplayer/ndmirror.tns --backup C:\path\to\backups
```

Downloads get timestamped filenames and metadata containing their source path,
size and SHA-256. Existing PC logs are never overwritten. Listing and downloads
do not modify calculator files. The explicit `put` command saves an existing
destination to the backup folder, uploads the named file, then downloads it
again and requires a byte-for-byte match. It never deletes or renames calculator
files. Transfers are limited to 8 MiB for this app/log tool. The calculator app
must be closed for replacement and must be opened manually afterward.
Directory enumeration and file-transfer commands were
implemented using libnspire's file-service reference above. This tool is not
part of the mirror's streaming session.

On the tested CX II OS 6.2, directory entries use response `10` and enumeration
ends with `FF11`. Full file-data transfers contained one zero byte beyond the
1472-byte NNSE length. Native messages whose length is divisible by 64 carry
one extra byte with an unspecified value, as documented in
[Firebird's CX II USB implementation](https://github.com/nspire-emus/firebird/blob/b10f3b51a9ab8e27d2703444b5e6bd278d4c5e6e/core/usblink_cx2.cpp#L352).
The mirror and file tool remove that byte only from a complete, checksummed
envelope at a whole-transfer boundary. Zero trailers remain accepted for
compatibility with observed file transfers. Partial and concatenated messages
remain untouched, and corrupt packets are rejected. A malformed length
error includes the declared size, buffered byte count and outer header for diagnosis.
Downloading a 3443-byte receiver log was verified on the
calculator. Replacing the 707432-byte receiver with a 709436-byte build also
passed byte-for-byte read-back, with the previous receiver backed up locally.
Classic CX file transfer has not been physically tested.

Retrieval while the receiver owned the foreground timed out in a physical test;
exit it before using the OS file service. An old request can then deliver a late
reply. Each file-tool connection therefore uses a fresh native host endpoint,
so a late response for an earlier transfer cannot be mistaken for the new file.

`CAP_PAUSE_PEER_CLOSE` identifies receivers whose callbacks remain on their native
channel after acknowledging Pause. The PC sends one explicit ECLOSE and services
final native messages before releasing its USB handle. A Pause acknowledgement
confirms the playback queue is paused; it does not certify that the native channel
has been deleted. Legacy receivers without this capability retain their previous
Pause-close behavior. Use the matching sender with receivers advertising this flag.
