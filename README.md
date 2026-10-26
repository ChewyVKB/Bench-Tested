# Label Printer Tester

A small desktop app for Ubuntu that tests **Zebra** and **Dymo** label printers: plug one in, click **Print test label**, and get a full-size, timestamped test label that shows whether the printer really works.

I built it for testing used printers before reselling them. It's just as useful for checking a printer after repairs, or for setting up a label station.

![Dymo test labels](docs/dymo-test-labels.png)

## Features

**General**
- Connected Zebra and Dymo printers appear automatically over USB. There's nothing to configure.
- **Print several printers at once:** tick them in the list (Zebras and Dymos mixed) and click **Print checked**. Each one prints with its own settings and shows its own status.
- Every test label is **timestamped**, so you have proof of when each unit was tested.
- Each test label includes:
  - a scannable barcode
  - a **printhead check band** of thin diagonal lines that crosses every dot on the printhead, so a dead dot shows as a line of gaps without the power draw of a solid black block
- **Clean up printers** removes old USB printer entries for units that are no longer connected. Network and office printers are never selected automatically.

**Zebra (ZPL and EPL)**
- Talks to the printer directly over USB. No driver needed.
- **Factory reset** before testing, which also re-measures the labels.
- **Auto-detects the label size** and resolution, then prints a test label that fills the label. The layout adapts to the size, from 1 in to 4×6.
- 4×6 and other tall labels also show the printer's **serial number, head usage (odometer) and firmware**, which is handy for listings.
- Buttons for printer info, the printer's own config label, calibration, and factory reset.
- Older EPL printers (LP/TLP 2844) are supported, with the label size picked manually.

**Dymo LabelWriter**
- Installs the Dymo driver (`printer-driver-dymo`) the first time it's needed.
- Each model is set up **once**. Every unit of that model you plug in afterward is ready immediately.
- Pick the loaded roll by **Dymo item number** (30251/30252 Address, 30256 Shipping, 30334, …). The choice is remembered per model.
- With several identical Dymos plugged in at the same time, each one automatically gets its own entry so every label comes out of the right printer.
- Shows a warning if a job gets stuck in the print queue, plus a button to clear it.

## Requirements

- Ubuntu 22.04 or newer (other Debian-based distros with GNOME/GTK should work too)
- Python 3 with GTK 3 bindings (`python3-gi`, `gir1.2-gtk-3.0`), which come preinstalled on Ubuntu Desktop
- CUPS (preinstalled), used for Dymo printers

## Install

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
./install.sh
```

The installer:
- copies the app to `~/.local/bin`
- adds **Label Printer Tester** to your app menu
- installs any missing requirements
- adds you to the `lp` group so the app can talk to USB printers

**Log out and back in once** after the first install.

To uninstall: `./uninstall.sh`

## Usage

1. Plug in a printer and turn it on. It shows up in the list on the left. If it doesn't, click **Refresh printers**.
2. Select it and choose its options:
   - **Zebra:** what to do before printing (factory reset, calibration, or nothing). Label size and resolution are detected automatically.
   - **Dymo:** which labels are loaded.
3. Click **Print test label**. To test several at once, tick them and click **Print checked**.

Everything that happens is shown in the **Activity** panel.

## Troubleshooting

| Problem | Fix |
|---|---|
| Zebra shows "not available as /dev/usb/lp*" | Unplug it and plug it back in. If that doesn't help, run `sudo modprobe usblp` |
| "No permission to use /dev/usb/lp…" | Run `sudo usermod -aG lp $USER`, then log out and back in |
| Zebra faults (red light) partway through a label | Usually a weak or non-original power supply. Check the power brick. |
| Dymo says "sent" but nothing prints | Click **Set up again**, then **Clear print queue**, and check that labels are loaded |
| Dymo LabelWriter 550 series | Not supported well on Linux. These only print on genuine RFID-chipped Dymo rolls. |
| Zebra label size not detected | Run **Calibrate**, or pick the size manually. The printer's raw reply is saved in `~/.cache/label-printer-test-last.txt` |

## How it works

- **Zebra:** sends ZPL/EPL straight to `/dev/usb/lp*`.
  - Factory reset: `^JUF` then `^JUS`
  - Calibration: `~JC`
  - Label size and info: read from the printer's `^HH` configuration reply
- **Dymo:** builds a PostScript page at the exact label size and prints it through CUPS with the official Dymo driver.
- **Settings:** stored in `~/.config/label-printer-test.json`.

The whole app is a single Python file (`label-printer-test`) with no dependencies beyond what Ubuntu ships.

## License

MIT. See [LICENSE](LICENSE).
