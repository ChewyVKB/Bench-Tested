#!/usr/bin/env bash
# Removes Bench Tested. Printers it set up in CUPS are left alone - remove them
# first with "Clean up printers…" in the app if you want.
set -eu
rm -rf "$HOME/.local/share/bench-tested/lib"
rm -f "$HOME/.local/bin/bench-tested" "$HOME/.local/share/applications/bench-tested.desktop"
read -r -p "Also delete saved settings and reset codes? [y/N] " a
case "$a" in [yY]*)
    rm -rf "$HOME/.local/share/bench-tested"
    rm -f "$HOME/.config/bench-tested.json" "$HOME/.cache/label-printer-test-last.txt" ;;
esac
echo "Bench Tested removed."
