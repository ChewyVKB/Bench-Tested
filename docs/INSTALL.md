# Installation

[← Back to README](../README.md)

## Contents

- [Before you start](#before-you-start)
- [Install](#install)
- [What the installer changes](#what-the-installer-changes)
- [Device access](#device-access)
- [Updating](#updating)
- [Uninstalling](#uninstalling)

## Before you start

| You need | Notes |
|---|---|
| Ubuntu 22.04 or newer | Other Debian-based GNOME desktops should work too |
| An account that can use `sudo` | Needed once, to install packages and grant device access |
| `git` | Install it with `sudo apt install git` if it's missing |
| An internet connection | Only for the first install, to download missing packages |

## Install

**1. Download the app**

```bash
git clone https://github.com/ChewyVKB/Bench-Tested.git
cd Bench-Tested
```

**2. Run the installer**

```bash
./install.sh
```

Enter your password when asked. The installer prints each step as it goes and finishes with `Done!`.

**3. Log out and back in once**

Ubuntu applies new group memberships only at login. Until you log back in, Zebra printing, scanner identification, mic buttons and signature pads may not work.

**4. Open the app**

Find **Bench Tested** in your app menu, or run:

```bash
bench-tested
```

Only one copy runs at a time, because two copies would fight over the devices. If you open it again, it tells you to switch to the window that's already open.

## What the installer changes

| What | Where |
|---|---|
| The app itself | `~/.local/share/bench-tested/lib/` |
| The `bench-tested` command | `~/.local/bin/bench-tested` |
| The app-menu entry | `~/.local/share/applications/bench-tested.desktop` |
| Device access rule | `/etc/udev/rules.d/70-bench-tested.rules` |
| Group memberships | `lp`, `input`, `dialout` (see below) |

The installer also adds any of these packages that are missing. It installs them one at a time, so a package that can't be found won't stop the rest:

| Package | Used for |
|---|---|
| `zint` | Drawing 2D barcode test codes (QR, Data Matrix, PDF417) |
| `sane-utils` | Document scanners |
| `alsa-utils`, `pipewire-bin` | Finding and using each device's own mic and speaker |
| `gir1.2-gstreamer-1.0`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-base`, `gstreamer1.0-pipewire` | Recording, the live level meter and the camera preview |
| `python3-gi-cairo` | Drawing signatures |
| `v4l-utils` | Camera modes and controls |
| `cups-client` | Dymo printers |

The Dymo driver (`printer-driver-dymo`) is installed later, the first time you set up a Dymo.

If you used the older **Label Printer Tester**, the installer replaces it. Your saved settings carry over.

## Device access

The installer adds your account to three groups. You can remove yourself from any of them later; only the related feature stops working.

| Group | Why it's needed | Without it |
|---|---|---|
| `lp` | Sending labels straight to Zebra printers | Zebra printing doesn't work |
| `input` | Telling scanners apart, and reading dictation mic buttons | Scans still work if you click the scan box first and then scan. Mic buttons can't be tested. |
| `dialout` | Reading serial-mode scanners and signature pads | Those devices can't be read |

The udev rule lets **the person logged in at the computer** read dictation mics and Topaz signature pads directly. It covers only those devices, not keyboards or anything else.

> **Shared computers:** members of the `input` group can read every keyboard. That's normal for a dedicated test bench. On a shared computer you may want to leave it out.

## Updating

```bash
cd Bench-Tested
git pull
./install.sh
```

Close the app first, or restart it afterwards. Settings and saved reset codes are kept.

## Uninstalling

```bash
cd Bench-Tested
./uninstall.sh
```

The uninstaller removes the app, the menu entry, the command and the udev rule. It asks whether to delete your saved settings and reset codes too.

It leaves alone:
- **Printers it set up in CUPS.** Remove them first with **Clean up printers…** in the app if you like.
- **Group memberships.** Remove one with `sudo gpasswd -d $USER <group>`.
- **Your proof files** in `~/Pictures/Bench Tested/`.
