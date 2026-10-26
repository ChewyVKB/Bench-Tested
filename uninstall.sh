#!/usr/bin/env bash
# Removes Label Printer Tester. Printers it set up in CUPS are left alone -
# remove those first with "Clean up printers..." in the app if you want.
set -eu
rm -f "$HOME/.local/bin/label-printer-test"
rm -f "$HOME/.local/share/applications/label-printer-test.desktop"
read -r -p "Also delete saved settings (~/.config/label-printer-test.json)? [y/N] " a
case "$a" in [yY]*) rm -f "$HOME/.config/label-printer-test.json" "$HOME/.cache/label-printer-test-last.txt" ;; esac
echo "Label Printer Tester removed."
