# Changelog

All notable changes to Bench Tested are listed here.

## [Unreleased]

### Added
- **Test certificates.** A PDF for every unit, pass or fail:
  - asset tag (scanned into the Asset tag box with any scanner, or typed) and certificate number
  - make, model, serial number and everything the device reports about itself
  - every test with its result, plus evidence and the unit's timestamped activity log
  - technician sign-off, company name and logo
  - a line in a CSV register for each certificate; units that were tested and unplugged without one get a certificate automatically
  - devices that don't report a serial number get a Serial no. box: scan it from the label after the asset tag, or type it
  - the model name can be corrected on the certificate when a device reports a generic one
- Conference speakerphones and expansion mics without a camera (Logitech, Poly/Polycom, Jabra, EPOS) are now detected. They get an audio-only page.
- Conference cams built as a hub with separate audio and video parts (e.g. Logitech ConferenceCam Connect) are detected.
- Topaz USB HID signature pads (`06a8:0043`, *Topaz HID Tablet*): live ink, hover dot, edge check and saved signatures, the same as serial pads.
- Signature pads: the drawing clears itself 4 seconds after you stop writing, like the pad's own screen. The checks stay, and Save signature still saves the last signature.

### Fixed
- Signature pads: the right edge check now passes. It was measured against an estimated writing area that was wider than the pad (x up to 2650; the T-LBK462 stops at about 2270), and saving a signature stored that estimate.
- Topaz BSB pads whose USB-serial chip still has the generic FTDI name and serial number (e.g. *FT232R USB UART*, serial *AH012S4C*) now show up as signature pads.
- PowerMicII-NS units with an Interlink *FSR 4ZUSB* trackball (`214a:0002`) get the trackball check, like units with the usual trackball.
- Nuance PowerMic 4: its buttons are now detected, and its built-in trackball's clicks count as trackball checks instead of mic buttons. Only this model is affected.
- Barcode scanners are only held while Bench Tested is the active window. In any other window (a browser, an ERP system) scanners type as normal, so the app can stay open all day.
- Zebras whose USB connection doesn't come back after a factory reset or calibration are reconnected in software, instead of needing a replug. Needs `./install.sh` to be run again once.
- Signature pads: the hover dot disappears when the pad stops reporting the pen, instead of staying frozen on screen.

## [2.0] Bench Tested, 2026-10-27

The Label Printer Tester grew into a testing bench for every kind of office device it's used to sell.

### Added
- **Device hub.** Plugged-in devices appear automatically, grouped by type, and each has its own page. Several devices can be tested at once, with a shared, timestamped Activity log.
- **Barcode scanners.**
  - Live per-scanner scan view.
  - 1D and 2D test codes with automatic checks.
  - Printable test sheet for laser scanners.
  - Factory-reset code library per model.
- **Dictation mics.**
  - 3-second record and playback through the mic's own speaker, with a live level meter.
  - Button checklist, including buttons sent as raw HID reports.
  - Trackball test.
- **Document scanners.** Batch scanning of the whole feeder, with blank-page and streak detection. Every page is saved as proof.
- **Topaz signature pads.**
  - Live ink and a hover dot, plus a four-edge check and saved signatures.
  - Supports both BSB (19200 7O1) and eSign (115200 8N1) models, and finds the right settings automatically for unknown models.
- **Cameras and conference hubs.**
  - Live preview and camera controls.
  - Pan/tilt/zoom test.
  - Speaker → mic tone check.
  - Saved photos.
- **Single instance.** Only one copy runs at a time, so two copies can't fight over devices.
- A udev rule that gives the logged-in user access to dictation mics and Topaz pads.
- A complete guide with installation, user and troubleshooting docs.

### Changed
- Renamed from **Label Printer Tester** to **Bench Tested**. The installer replaces the old app and keeps its settings.
- Every fresh plug-in is treated as a new unit, so swapping identical units in the same port works reliably.
- Device connections that appear late or fail right after plug-in are retried every second.

## [1.0] Label Printer Tester, 2026-10-26

### Added
- Zebra ZPL/EPL test labels at any size, with automatic label-size and resolution detection.
- Zebra factory reset, calibration, configuration label, and printer info (serial number, head usage, firmware).
- Dymo LabelWriter support through CUPS, with one-time driver setup per model and roll selection by Dymo item number.
- Test labels with date, time, barcode and a printhead check band.
- Printing on several printers at once.
- Clean-up of stale printer entries.

[2.0]: https://github.com/ChewyVKB/Bench-Tested/tree/main
[1.0]: https://github.com/ChewyVKB/Bench-Tested/tree/001287e
