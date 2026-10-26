#!/usr/bin/env bash
# Installs Label Printer Tester for the current user (no root needed except for
# missing system packages and the one-time printer permission).
set -euo pipefail
cd "$(dirname "$0")"

BIN="$HOME/.local/bin/label-printer-test"
DESKTOP="$HOME/.local/share/applications/label-printer-test.desktop"

echo "==> Checking requirements"
pkgs=()
if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    pkgs+=(python3-gi gir1.2-gtk-3.0)
fi
command -v lpstat >/dev/null || pkgs+=(cups-client)
if ((${#pkgs[@]})); then
    echo "    Installing: ${pkgs[*]}"
    sudo apt-get install -y "${pkgs[@]}"
fi

echo "==> Installing the app"
install -Dm755 label-printer-test "$BIN"
mkdir -p "$(dirname "$DESKTOP")"
sed "s|@BIN@|$BIN|" label-printer-test.desktop.in > "$DESKTOP"
update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true

relog=0
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx lp; then
    echo "==> Allowing $USER to talk to USB label printers (adds you to the 'lp' group)"
    sudo usermod -aG lp "$USER"
    relog=1
fi

echo
echo "Done! Open 'Label Printer Tester' from your app menu, or run: label-printer-test"
if ((relog)); then
    echo "IMPORTANT: log out and back in once so the printer permission takes effect."
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
    echo "Note: ~/.local/bin isn't on your PATH - the app menu entry still works." ;;
esac
