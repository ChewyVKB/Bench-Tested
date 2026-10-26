# Bench Tested

A desktop testing bench for Ubuntu. Plug in a device, pick it from the list, and test it. Every test leaves **timestamped proof** that the unit works.

I built it for testing used equipment before reselling it. It's just as handy for checking gear after repairs or setting up a workstation.

**Supported devices right now:**
- **Label printers:** Zebra (ZPL/EPL) and Dymo LabelWriter
- **Barcode scanners:** Symbol/Zebra, Honeywell, Datalogic, Opticon, Newland, and other USB scanners

**Coming next:** cameras and conference hubs, dictation microphones, signature pads, document scanners.

![Dymo test labels](docs/dymo-test-labels.png)

## Features

### Everywhere
- Plugged-in devices appear on their own, grouped by type. Each device has its own page.
- One shared **Activity** log, and a live status line for every device.
- Several devices can work at the same time. Only the device that's busy is locked.

### Label printers
- **Zebra:**
  - **factory reset** (also re-measures the labels)
  - **auto-detects label size and resolution**
  - prints a full-size test label at any size
  - shows the **serial number, head usage (odometer) and firmware**
- **Dymo:**
  - installs the driver the first time it's needed, and sets each model up **once**
  - pick the loaded roll by **Dymo item number** (30251/30252, 30256, 30334…)
- Every test label includes:
  - the date and time
  - a scannable barcode
  - a **printhead check band** that crosses every dot on the printhead, so dead dots show up as gaps without the power draw of a solid black block
- **Print checked** prints on all ticked printers at once. Zebras and Dymos can be mixed, each with its own settings.
- **Clean up printers** removes entries for printers that are no longer connected. Office and network printers are never selected automatically.

### Barcode scanners
- **Live scan view per scanner.** Scans are labeled with the scanner that sent them, so you can test several at once. Scans don't leak into other windows.
- **Test codes** on screen, with automatic ✓ checks:
  - 1D: Code 128, Code 39, UPC-A, EAN-13
  - 2D: QR, Data Matrix, PDF417
- A summary shows whether a scanner reads 1D only or 1D and 2D.
- **Print a test sheet** on a Zebra (as many labels as needed) or on any office printer. Laser scanners can't read screens, so they need the printed sheet.
- Shows how the scanner is connected (USB keyboard, serial/COM port, or HID POS) and warns when it isn't sending scans as typing.
- **Factory reset code library:** add a model's reset barcode once (a screenshot or PDF from its manual), and it shows up automatically for every scanner of that model.

## Requirements

- Ubuntu 22.04 or newer (other Debian-based GNOME desktops should work)
- Python 3 with GTK 3 (`python3-gi`, `gir1.2-gtk-3.0`), preinstalled on Ubuntu Desktop
- CUPS (preinstalled), used for Dymo printers
- `zint` (installed by the installer), which draws the 2D test codes

## Install

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
./install.sh
```

The installer:
- copies the app to `~/.local/share/bench-tested`
- adds **Bench Tested** to your app menu, plus a `bench-tested` command
- installs anything missing
- gives your user access to USB devices (see the next section)

**Log out and back in once** after the first install.

To uninstall: `./uninstall.sh`

### About device access

The installer adds you to three groups. You can skip any of them; the related feature just works more simply.

| Group | Why | Without it |
|---|---|---|
| `lp` | Talk to Zebra printers over USB | Zebra printing doesn't work |
| `input` | See which scanner sent each scan | Scans still work: click the box on the scanner page and scan |
| `dialout` | Read scanners set to serial/COM-port mode | Those scanners can't be read |

Being in `input` lets programs you run read keyboards and other input devices. That's normal for a dedicated test bench. Skip it on a shared computer if that's a concern.

## Usage

1. Plug in a device. It shows up on the left. If it doesn't, click **Refresh devices**.
2. Click it to open its page.
3. Run its test:
   - **Printers:** click **Print test label**, or tick several printers and click **Print checked**.
   - **Scanners:** scan the test codes on screen or on a printed sheet, and watch the ✓ marks fill in.

## Troubleshooting

| Problem | Fix |
|---|---|
| Zebra shows "not available as /dev/usb/lp*" | Unplug it and plug it back in. If that doesn't help, run `sudo modprobe usblp` |
| "No permission to use /dev/usb/lp…" | Run `sudo usermod -aG lp $USER`, then log out and back in |
| Zebra faults (red light) partway through a label | Usually a weak or non-original power supply |
| Dymo says "sent" but nothing prints | Click **Set up again**, then **Clear print queue**, and check that labels are loaded |
| Dymo LabelWriter 550 series | Poor Linux support. These only print on genuine RFID-chipped Dymo rolls. |
| Scanner listed, but scans don't appear | It may be in HID POS/OPOS mode. Scan its **factory reset** or "USB keyboard" code. |
| Laser scanner won't read the screen | Normal. Use **Print test sheet…** |

## How it works

- **Zebra:** ZPL/EPL sent straight to `/dev/usb/lp*`.
  - Factory reset: `^JUF` then `^JUS`
  - Calibration: `~JC`
  - Label size and info: read from the printer's `^HH` configuration reply
- **Dymo:** a PostScript page at the exact label size, printed through CUPS with the official Dymo driver.
- **Scanners:**
  - keyboard-mode scanners are read from `/dev/input` and kept from typing into other windows while Bench Tested runs
  - serial-mode scanners are read from `/dev/ttyACM*`
- **Settings:** stored in `~/.config/bench-tested.json`. Reset codes are stored in `~/.local/share/bench-tested/reset-codes/`.

Pure Python + GTK, with no dependencies beyond what Ubuntu ships (plus `zint` for 2D codes).

## License

MIT. See [LICENSE](LICENSE).
