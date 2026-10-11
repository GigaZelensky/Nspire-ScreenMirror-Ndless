# ND Screen Mirror

Stream your PC screen to a TI-Nspire over USB, and control the PC with the calculator's touchpad and keyboard.

Mirror one of your monitors, or give the calculator its own Windows display. You can put an app, video or game on that display while keeping the rest of your desktop on your regular monitors. The calculator shows the picture in colour at 320×240.

Made by **GigaZelensky**.

## Getting started

For Windows, you'll need:

- A TI-Nspire with Ndless and a normal USB cable.
- Windows 10 or 11, 64-bit.

1. Download the Windows ZIP from [Releases](https://github.com/GigaZelensky/Nspire-ScreenMirror-Ndless/releases) and extract it.
2. Copy `ndmirror.tns` to the calculator and open it.
3. Connect the calculator to your PC. No OTG adapter is needed.
4. If you use TI-Nspire PC software or Computer Link, close it so the USB connection is available.
5. Open `PC/ND-Screen-Mirror.exe` and press **Start mirroring**.

Keep the PC executable beside its `_internal` folder. The calculator should show **Waiting for the PC** until the sender connects.

CX II connects directly using Windows' built-in USB driver. You don't need to install TI software.

| Platform | Status |
| --- | --- |
| Windows + TI-Nspire CX II | Tested |
| Original TI-Nspire CX | Awaiting testing; direct Windows connection requires a WinUSB-compatible driver |
| Linux (X11 / Wayland) | Screen capture and keyboard/mouse implemented; physical USB and Raspberry Pi testing pending |

Audio streaming isn't supported.

## A separate display

In **Picture**, enable **Own Windows display**. Click **Set up display driver** the first time; Windows will ask for administrator approval to install the included [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver).

Move a window onto it with **Win+Shift+Arrow**, or select that display in a game's settings. You can arrange it alongside your other monitors in Windows Display settings.

| Desktop size | Resolution |
| --- | --- |
| 1× | 320×240 |
| 1.5× — default | 480×360 |
| 2× | 640×480 |
| 2.5× | 800×600 |
| 3× | 960×720 |
| 4× | 1280×960 |
| Custom | 120–4096 pixels per dimension |

If an app feels cramped, choose a larger desktop size. Windows gets more room to lay out the app, and the result is scaled down to the calculator. Different aspect ratios use black bars.

**Stop** pauses mirroring and keeps this display open. Closing the PC app removes it and moves its windows back to your other monitors.

## Settings

The defaults are **H.264**, **30 FPS**, **medium** encoding and **automatic quality**. Your choices are saved between launches, including custom desktop sizes, sensitivities and the selected settings tab.

Press **Stop** before changing settings, then **Start mirroring** to apply them.

| Setting | What it does |
| --- | --- |
| Picture mode | H.264 and MPEG-4 are for video. Lossless desktop preserves detail in still images and text. |
| Frame rate | Sets the target updates per second. |
| Encoding preset | Controls PC compression effort. MPEG-4 fast uses better block decisions; medium also uses trellis quantization for a little more detail at Q1. Try ultrafast if PC encoding is too slow. |
| Automatic quality | Adjusts H.264 bitrate or MPEG-4 Q to keep up with the selected frame rate, then restores detail when there's room. MPEG-4 starts at Q1; static timing noise does not lower its quality. |
| Quality | Turn off automatic quality to show H.264's CRF control. Lower CRF gives a clearer picture. MPEG-4 has its own Q control, also lower for clearer. |
| Calculator clock | Pick a preset or type MHz: CX II supports 396–492 and CX supports 132–240, in steps of 12. Entry clock keeps the speed the receiver opened with; it is restored on exit. |
| Capture method | Chooses how the PC captures the picture. The separate Windows display uses GDI automatically. |
| Pointer and scroll sensitivity | Movement speed: 1 is normal, 0.5 is half and 2 is double. |
| Reverse scrolling | Reverses the scroll direction. By default, content follows your swipe. |
| Keep pointer on mirrored display | Stops the calculator-controlled pointer leaving that display. Off by default. |

Picture settings are in **Picture**, input settings in **Controls**, and the clock, capture method and connection test pattern in **Advanced**.

## Calculator controls

Enable **Control this PC with the calculator** in Controls.

| Calculator key or gesture | PC action |
| --- | --- |
| Touchpad swipe | Move the pointer |
| Touchpad centre | Left click; hold to drag |
| Menu | Right click |
| Hold Scratchpad + vertical swipe | Scroll |
| Letters, digits, Space, Enter, Del, Tab, Esc, arrows and operators | Corresponding keyboard keys |
| Shift / Ctrl / Doc | Shift / Ctrl / Alt |
| Doc+Tab | Alt+Tab — switch PC windows |
| Ctrl+Tab | Ctrl+Tab — switch tabs in apps that support it |

The pointer can move across all your monitors unless you enable **Keep pointer on mirrored display**.
Leave that option off for games: swipes send relative mouse movement, which works with camera controls. Keyboard input uses scan codes for games as well as desktop apps.

| Shortcut | Calculator action |
| --- | --- |
| Flag | Show or hide FPS and clock stats |
| Ctrl+Esc | Exit the receiver |
| Ctrl+D | Enable diagnostic logging for this session and save a snapshot |

When mirroring is paused or PC control is off, Esc and D work without Ctrl.
The calculator FPS overlay uses the sender's wall-clock measurement of displayed frames. It shows `--.-` until the first measurement arrives. Update both apps for matching counters; older receivers use an OS-tick estimate that can read too high during decoding.

Diagnostic files are off by default. Enable **Save diagnostic logs** in the PC app's Advanced tab when troubleshooting; Ctrl+D enables calculator logs for the current receiver session. Normal builds do not capture video or run the developer memory probes.

## Troubleshooting

- **Still waiting for the PC:** check the cable and close other TI software that might be using the connection.
- **Empty separate display:** move a window onto it with Win+Shift+Arrow.
- **Low FPS:** try the ultrafast preset with automatic quality enabled, or lower the frame rate.
- **Pointer or scrolling too fast:** lower its sensitivity under Controls.
- **An error or unexpected reconnect:** keep `%LOCALAPPDATA%/ND Screen Mirror/logs/connection.log` from the PC. Press Ctrl+D before exiting the receiver, then keep both `ndmirror.log.tns` and `ndmirror-event.log.tns`. The event file preserves the snapshot; the other file records exit progress. Both include timestamps and session IDs. Decoder failures also save an event snapshot automatically.

## Running from source

Install Python **3.11+** with Tk support, then:

```sh
python -m pip install -r requirements.txt
python pc/mirror.py
```

Linux supports X11 and Wayland capture with calculator keyboard/mouse controls. Select **French (AZERTY)** under **Controls → Host keyboard layout** if the desktop uses that layout. For Debian and Raspberry Pi OS, follow the [Linux setup guide](docs/linux.md), or run `bash tools/setup_linux.sh` from the checkout. It installs the capture dependencies and configures USB and virtual input access.

```sh
.venv/bin/python pc/mirror.py --cli --fps 30
```

On Wayland, choose the monitor in the desktop's sharing dialog. Linux uses libusb directly; no libnspire build is required. Creating a separate monitor and pointer confinement remain Windows features.

[Build instructions and developer notes](docs/development.md) cover SDK setup, checks and packaging. The [vendor overview](vendor/README.md) explains the reused calculator code.

## Builds and releases

[GitHub Actions](https://github.com/GigaZelensky/Nspire-ScreenMirror-Ndless/actions) builds the receiver and Windows sender. The **latest** release follows the default branch; `v*` tags produce versioned releases. Each release includes the Windows ZIP, `ndmirror.tns` and checksums.

## License

GPL-3.0. Dependencies keep their own licenses; see [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES).
