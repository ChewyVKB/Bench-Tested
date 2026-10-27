# How it works

[← Back to README](../README.md)

Technical notes for the curious, and for anyone adding support for a new device.

## Contents

- [Overview](#overview)
- [Project layout](#project-layout)
- [Device detection](#device-detection)
- [Label printers](#label-printers)
- [Barcode scanners](#barcode-scanners)
- [Dictation mics](#dictation-mics)
- [Document scanners](#document-scanners)
- [Signature pads](#signature-pads)
- [Cameras and conference hubs](#cameras-and-conference-hubs)
- [Settings and files](#settings-and-files)
- [Adding a new device type](#adding-a-new-device-type)

## Overview

Bench Tested is plain **Python 3 + GTK 3** (PyGObject). It has no Python dependencies beyond what Ubuntu ships. It talks to hardware through standard Linux interfaces (sysfs, `/dev/usb/lp*`, `/dev/input`, `/dev/hidraw*`, serial ports, V4L2) and a few standard tools (CUPS, SANE, GStreamer, PipeWire).

Every device test runs on its own background thread. Results are handed back to the GTK main loop with `GLib.idle_add`, so the window never freezes. Only the device that's busy is locked.

## Project layout

```
Bench-Tested/
├── bench-tested              # launcher
├── benchtested/
│   ├── app.py                # main window, device list, Activity log, single-instance lock
│   ├── common.py             # USB scanning (sysfs), settings, shared helpers
│   ├── gtkutil.py            # small GTK helpers
│   ├── printers.py           # Zebra and Dymo
│   ├── barcode.py            # barcode scanners, test codes, test sheets, reset codes
│   ├── inputdev.py           # evdev and raw HID readers
│   ├── audio.py              # finding, recording and playing a device's own audio
│   ├── mic.py                # dictation mics
│   ├── docscan.py            # document scanners
│   ├── sigpad.py             # Topaz signature pads
│   └── camera.py             # cameras and conference hubs
├── 70-bench-tested.rules     # udev rule (mic and pad access)
├── install.sh / uninstall.sh
└── docs/
```

## Device detection

`/sys/bus/usb/devices` is scanned every second. Each USB device is sorted into a category (printer, scanner, mic, docscan, sigpad, camera) by vendor and product ID, USB interface class and product name.

- A unit is identified by its **serial number**, or by its USB port when it has none.
- Each **plug-in** is identified separately (port + USB device number). Unplugging a unit and plugging in another identical one gives a fresh result, even in the same port.
- Linux nodes such as `/dev/input/event*` and `/dev/ttyUSB*` often appear a moment after the USB device, and permissions may arrive later still. Connections are retried every second until they succeed.

## Label printers

**Zebra.** Commands go straight to `/dev/usb/lp*`, without a print queue.

| Action | Command |
|---|---|
| Label size, resolution, serial, odometer, firmware | `^HH` configuration reply |
| Factory reset | `^JUF`, then `^JUS` sent separately |
| Calibration | `~JC` |
| Test label | ZPL (or EPL for older models) built for the exact label size and dpi |

**Dymo.** Printed through CUPS with the official `printer-driver-dymo`. Each model gets one queue, using a USB address without the serial number so any unit of that model uses it. When two identical units are plugged in at once, a separate queue is added for each. The label is a PostScript page at the exact label size.

**Printhead check band.** Thin diagonal lines that span the full width of the head, so between them they hit every dot. A dead dot leaves a white line. Unlike a solid black block, it has no heavy black areas, so it won't trip a weak power supply.

## Barcode scanners

- **Keyboard (HID) mode.** Read from `/dev/input/event*` with an exclusive grab, so scans never type into other windows and each scan is tagged with the scanner that sent it.
- **Serial mode.** Read from `/dev/ttyACM*`.
- **HID POS mode.** Detected and reported, with a suggestion to switch to keyboard mode.
- **Test codes** are drawn with `zint`. A built-in Code 128 encoder is the fallback when `zint` is missing.
- **Test sheets** are ZPL spread over as many Zebra labels as needed, or a PostScript page for office printers.
- **Reset codes** are images or PDFs stored per model and shown for every unit of that model.

## Dictation mics

- **Audio.** The mic's own sound card is matched through sysfs and PipeWire (`pw-dump`). It's recorded and played back with GStreamer, falling back to `arecord` / `aplay`. The computer's built-in mic is never used.
- **Buttons.** Read from evdev, plus the mic's raw HID reports (`/dev/hidraw*`). Raw reports are decoded into press and release events. A button has to be held for 40 ms to count, and analog trackball and slider data is filtered out.
- **Trackball.** On PowerMics it's a sibling USB function, found and read alongside the main device.

## Document scanners

`scanimage` (SANE) runs in batch mode, writing one PNG per page, and each page is shown as soon as it lands.

- **Blank page:** almost no ink.
- **Streak:** a run of columns that are dark from top to bottom. On a blank sheet that can only come from the scanner itself.

## Signature pads

Topaz serial pads use an FTDI USB-serial chip (`0403:6001`) whose serial number starts with `TOPAZ` or `eSign`. The port is configured with `stty`, including `clocal` so the modem carrier line is ignored.

Pen data arrives as **5-byte packets**:

| Byte | Content |
|---|---|
| 1 | Status: bit 0 = pen touching, bit 6 = pen in range (hover) |
| 2–3 | X, low then high, 7 bits each |
| 4–5 | Y, low then high, 7 bits each |

Models differ only in port speed:

| Profile | Settings |
|---|---|
| BSB (serial `TOPAZ…`) | 19200 baud, 7 data bits, odd parity, 1 stop bit |
| eSign | 115200 baud, 8 data bits, no parity, 1 stop bit |

For an unknown model, each profile is tried in turn. The first one where at least 80% of packets decode cleanly is kept and remembered for that serial number.

## Cameras and conference hubs

- **Preview.** GStreamer `v4l2src` into an `appsink`, drawn in the window.
- **Modes and controls.** Read and set with `v4l2-ctl`. The PTZ test steps pan, tilt and zoom through their ranges, then back.
- **Speaker → mic check.** A tone is played through the device's speaker while its mic records. A Goertzel filter detects the tone's frequency in the recording.
- **Ignored on purpose:** built-in laptop cameras and dock audio.

## Settings and files

| What | Where |
|---|---|
| Settings (per unit and per model) | `~/.config/bench-tested.json` |
| Reset-code library | `~/.local/share/bench-tested/reset-codes/` |
| Proof files | `~/Pictures/Bench Tested/` |
| App | `~/.local/share/bench-tested/lib/` |

## Adding a new device type

1. **Detection:** teach `scan_usb()` in `common.py` to recognize the device and give it a category.
2. **Page:** add a module with a mixin class (see `MicMixin` or `SigPadMixin`), add it to `App` in `app.py`, and have it build the device's page and run its test on a background thread. Use `self.task()` so only that device is locked while it works.
3. **Status:** report results to the Activity log with `self.log()` and to the device's row in the list.
4. **Access:** if the device needs raw access, add a `uaccess` line for its vendor ID to `70-bench-tested.rules`.
5. **Docs:** add a section to the [user guide](USER-GUIDE.md) and an entry to the [changelog](../CHANGELOG.md).
