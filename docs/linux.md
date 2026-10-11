# Linux

The sender supports X11 and Wayland desktops, including Raspberry Pi OS's labwc desktop. Keyboard, mouse buttons, relative pointer movement, scrolling and sensitivity controls work through Linux virtual input devices. The calculator uses the same `ndmirror.tns` as Windows.

Linux integration checks run against real D-Bus and GStreamer, with simulated portal and USB endpoints. A physical calculator connection and Raspberry Pi performance still need testing. Creating a separate monitor and confining the pointer to it are currently Windows features.

## Debian / Raspberry Pi OS

Use a desktop session with Python 3.11 or newer. A 64-bit OS is recommended for the available encoding-library wheels.

```sh
git clone https://github.com/GigaZelensky/Nspire-ScreenMirror-Ndless.git
cd Nspire-ScreenMirror-Ndless
bash tools/setup_linux.sh
```

The script installs Python dependencies, libusb, GStreamer, PipeWire and the portal backend for your desktop. It creates an `ndmirror` group and installs rules for calculator USB access and `/dev/uinput`. Run it as your regular desktop user; it uses sudo for system setup.

After setup, run `newgrp ndmirror` to open a shell with the new group, or sign out and back in once. Unplug/reconnect the calculator. Copy `ndmirror.tns` from [Releases](https://github.com/GigaZelensky/Nspire-ScreenMirror-Ndless/releases) to the calculator and open it. Close TiLP or any other calculator linking app.

```sh
.venv/bin/python pc/mirror.py
```

Press **Start mirroring**. **Auto** capture selects the ScreenCast portal on Wayland or MSS on X11. On Wayland, select the monitor in the desktop's screen-sharing dialog; the app's numeric Monitor setting is used for X11. Approve sharing each time you Start. The shared pointer is included in the picture.

The usual calculator controls work: touchpad to move/click, Menu for right click, Scratchpad + swipe to scroll, and Doc for Alt. Linux sends physical key events, which the desktop interprets through its keyboard layout. In **Controls → Host keyboard layout**, select **French (AZERTY)** if the desktop uses a French keyboard. This maps calculator letters to the matching host positions, including M, A/Q and W/Z, and preserves Ctrl/Alt/Shift chords. Digits and punctuation follow the desktop's normal keys and modifiers. It does not rearrange the calculator keys or change the laptop's layout.

**Auto** uses `XKB_DEFAULT_LAYOUT` when it is exactly `us` or `fr`; otherwise it uses US positions. Desktop sessions do not always export this value, so select the layout explicitly if letters differ. The selection is saved. For a terminal launch, use `--keyboard-layout fr` or `--keyboard-layout us`. Stop, disconnect and exit release held keys and remove the virtual devices.

For a terminal launch:

```sh
.venv/bin/python pc/mirror.py --cli --fps 30
```

On slower Raspberry Pis, try `--preset ultrafast`; the default remains medium. Automatic quality is enabled. Use `--no-input` if you only want the picture; that does not require `/dev/uinput` access.

## Other distributions / manual setup

Install Python/Tk, development headers for the evdev build, libusb 1.0, PipeWire, `gst-launch-1.0`, the GStreamer base and PipeWire plugins, `xdg-desktop-portal`, and your desktop's portal backend. GNOME uses `xdg-desktop-portal-gnome`, KDE uses `xdg-desktop-portal-kde`, and labwc/sway use `xdg-desktop-portal-wlr`.

Create a virtual environment and install `requirements.txt`. Load the `uinput` module and give your user access to `/dev/uinput` and the calculator's USB device. The supplied `tools/70-ndmirror.rules` expects an `ndmirror` group. The application runs as your desktop user, including its portal connection.

The default USB backend uses libusb with the project's shared packet implementation. It does not require libnspire. The previous transport remains an explicit developer option: `--backend libnspire --library /path/to/libnspire.so`.

## Troubleshooting

- **No sharing dialog:** check that PipeWire and your desktop's portal backend are installed and running in your user session. On labwc/sway, the portal services need `WAYLAND_DISPLAY` and `XDG_CURRENT_DESKTOP` in their environment. After installing the backend, a desktop logout/login restarts them with the right environment. See the [wlr backend setup notes](https://github.com/emersion/xdg-desktop-portal-wlr#running).
- **Sharing cancelled:** press Start and approve the dialog. The sender does not switch to XWayland screenshots when sharing is denied.
- **No first frame:** check `gst-inspect-1.0 pipewiresrc` and `gst-inspect-1.0 videoscale`. The portal must support screen sharing with an embedded cursor.
- **Cannot create input devices:** check `test -w /dev/uinput`, `lsmod | grep uinput` and `id -nG`. Sign out/in after adding the group, or use `--no-input`.
- **USB permission/busy error:** reconnect after installing the rules and close other linking apps. Confirm your session belongs to `ndmirror`.
- **Need a log:** connection failures go to `${XDG_STATE_HOME:-~/.local/state}/ND Screen Mirror/logs/connection.log`. On the calculator, Ctrl+D requests its log, then Ctrl+Esc exits. Send both logs when reporting a failure.

## Implementation references

[ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html), [portal request lifecycle](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html), [PipeWire GStreamer plugin](https://github.com/PipeWire/pipewire/tree/master/src/gst), [python-evdev](https://python-evdev.readthedocs.io/en/latest/tutorial.html#injecting-input) and [python-libusb1](https://github.com/vpelletier/python-libusb1).
