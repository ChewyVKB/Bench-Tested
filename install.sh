#!/usr/bin/env bash
# Installs Bench Tested for the current user. Asks for your password only to
# install missing system packages and to give you access to USB devices.
set -euo pipefail
cd "$(dirname "$0")"

LIB="$HOME/.local/share/bench-tested/lib"
BIN="$HOME/.local/bin/bench-tested"
APPS="$HOME/.local/share/applications"

echo "==> Checking requirements"
pkgs=()
if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    pkgs+=(python3-gi gir1.2-gtk-3.0)
fi
command -v lpstat >/dev/null || pkgs+=(cups-client)
command -v zint >/dev/null || pkgs+=(zint)          # draws the barcode test codes
command -v scanimage >/dev/null || pkgs+=(sane-utils)   # document scanners
command -v arecord >/dev/null || pkgs+=(alsa-utils)     # dictation mic fallback
command -v pw-dump >/dev/null || pkgs+=(pipewire-bin)   # finds each device's mic/speaker
if ! python3 -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' 2>/dev/null; then
    pkgs+=(gir1.2-gstreamer-1.0)
fi
pkgs+=(gstreamer1.0-plugins-good gstreamer1.0-pipewire)  # recording + live level meter (usually already there)
pkgs+=(python3-gi-cairo)                                   # drawing signatures
command -v v4l2-ctl >/dev/null || pkgs+=(v4l-utils)        # camera modes and controls
pkgs+=(gstreamer1.0-plugins-base)                          # camera preview (usually already there)
missing=()
for p in "${pkgs[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if ((${#missing[@]})); then
    echo "    Installing: ${missing[*]}"
    for p in "${missing[@]}"; do      # one at a time, so one unavailable package can't block the rest
        sudo apt-get install -y "$p" >/dev/null || echo "    Couldn't install $p - the app still runs without it."
    done
fi

echo "==> Installing Bench Tested"
rm -rf "$LIB/benchtested"
mkdir -p "$LIB" "$(dirname "$BIN")" "$APPS"
cp -r benchtested "$LIB/"
find "$LIB/benchtested" -name '__pycache__' -prune -exec rm -rf {} +
install -m755 bench-tested "$BIN"
sed "s|@BIN@|$BIN|" bench-tested.desktop.in > "$APPS/bench-tested.desktop"

# replace the older Label Printer Tester (its settings carry over automatically)
rm -f "$HOME/.local/bin/label-printer-test" "$APPS/label-printer-test.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

echo "==> Device access"
relog=0
add_group() {   # group, reason
    if getent group "$1" >/dev/null && ! id -nG "$USER" | tr ' ' '\n' | grep -qx "$1"; then
        echo "    Adding $USER to '$1' - $2"
        sudo usermod -aG "$1" "$USER"
        relog=1
    fi
}
add_group lp      "talk to USB label printers"
add_group input   "see each scanner's scans and the dictation mic buttons"
add_group dialout "read serial-mode scanners and signature pads"

echo
echo "Done! Open 'Bench Tested' from your app menu, or run: bench-tested"
if ((relog)); then
    echo "IMPORTANT: log out and back in once so the new device access takes effect."
fi
