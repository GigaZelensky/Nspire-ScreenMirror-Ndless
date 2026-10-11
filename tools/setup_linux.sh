#!/usr/bin/env bash
# Debian/Raspberry Pi OS setup. Run as the desktop user, not with sudo bash.
set -euo pipefail
if [[ $(id -u) == 0 ]]; then
    echo "Run this as your desktop user: bash tools/setup_linux.sh" >&2
    exit 1
fi
command -v apt-get >/dev/null || { echo "This setup script requires Debian or Raspberry Pi OS." >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target_user=$(id -un)
desktop=${XDG_CURRENT_DESKTOP:-}
desktop=${desktop,,}
case "$desktop" in
    *gnome*) portal_backend=xdg-desktop-portal-gnome ;;
    *kde*) portal_backend=xdg-desktop-portal-kde ;;
    *labwc*|*sway*|*wayfire*|*wlroots*) portal_backend=xdg-desktop-portal-wlr ;;
    *) if [[ ${XDG_SESSION_TYPE:-} == wayland ]]; then
           portal_backend=xdg-desktop-portal-wlr
       else
           portal_backend=xdg-desktop-portal-gtk
       fi ;;
esac
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev python3-tk build-essential \
    libusb-1.0-0 linux-libc-dev pipewire gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-pipewire xdg-desktop-portal "$portal_backend"
sudo modprobe uinput
getent group ndmirror >/dev/null || sudo groupadd --system ndmirror
sudo usermod -aG ndmirror "$target_user"
printf 'uinput\n' | sudo tee /etc/modules-load.d/ndmirror.conf >/dev/null
sudo install -m 0644 "$repo_dir/tools/70-ndmirror.rules" /etc/udev/rules.d/70-ndmirror.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install -r "$repo_dir/requirements.txt"
echo "Setup complete. Run newgrp ndmirror (or sign out/back in) for group access, then reconnect USB."
echo "Open ndmirror.tns on the calculator. Run: .venv/bin/python pc/mirror.py"
