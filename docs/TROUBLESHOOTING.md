# Troubleshooting

[← Back to README](../README.md)

**Start here:** most problems come from one of these three things.

1. **You haven't logged out and back in since installing.** New device permissions only apply after a fresh login.
2. **The device needs a replug.** Unplug it, wait two seconds, and plug it back in.
3. **The answer is in the log.** The **Activity** log at the bottom of the window usually says exactly what went wrong.

## Contents

- [Devices don't show up](#devices-dont-show-up)
- [Zebra printers](#zebra-printers)
- [Dymo printers](#dymo-printers)
- [Barcode scanners](#barcode-scanners)
- [Dictation mics](#dictation-mics)
- [Document scanners](#document-scanners)
- [Signature pads](#signature-pads)
- [Cameras and conference hubs](#cameras-and-conference-hubs)
- [Collecting details for a bug report](#collecting-details-for-a-bug-report)

## Devices don't show up

| Problem | Fix |
|---|---|
| A device isn't in the list | Click **Refresh devices**. Check it's powered on and try another USB port or cable. |
| It shows in `lsusb` but not in the app | The device type may not be recognized yet. [Open an issue](https://github.com/ChewyVKB/Bench-Tested/issues) with the `lsusb` line. |
| "Bench Tested is already open" | Only one copy can run at a time. Switch to the open window. |

## Zebra printers

| Problem | Fix |
|---|---|
| "not available as /dev/usb/lp*" | Replug the printer. If that doesn't help, run `sudo modprobe usblp`. |
| "No permission to use /dev/usb/lp…" | Run `sudo usermod -aG lp $USER`, then log out and back in. |
| Wrong label size detected | Run **Calibrate**, then print again. Or pick the size by hand under **Label size**. |
| Prints garbage or the raw code | Switch **Printer language** to the other setting (ZPL or EPL). |
| Faults (red light) partway through a label | Usually a weak or non-original power supply. Try a genuine Zebra power brick. |
| White lines through the check band | Dead dots on the printhead. Clean the head with isopropyl alcohol and print again. If they're still there, the head is worn. |

## Dymo printers

| Problem | Fix |
|---|---|
| Says "sent" but nothing prints | Click **Set up again**, then **Clear print queue**. Check that labels are loaded and the roll is seated. |
| Print is cut off or misplaced | Make sure **Labels loaded** matches the roll that's actually in the printer. |
| Driver install fails | Check your internet connection, then run `sudo apt install printer-driver-dymo` yourself. |
| LabelWriter 550 series | These only print on genuine, chipped Dymo rolls and have poor Linux support. |

## Barcode scanners

| Problem | Fix |
|---|---|
| Listed, but scans don't appear | It may be in HID POS / OPOS mode. Scan its **factory reset** or "USB keyboard" code. |
| Scans appear but can't tell scanners apart | Log out and back in so the `input` group takes effect. |
| Laser scanner won't read the screen | That's normal: laser scanners can't read screens. Use **Print test sheet…** |
| Only Code 128 is shown | The `zint` tool is missing. Click **Install barcode tool** on the page. |
| Serial-mode scanner shows nothing | Log out and back in so the `dialout` group takes effect. |

## Dictation mics

| Problem | Fix |
|---|---|
| Buttons don't register | Log out and back in once after installing. If still nothing, replug the mic. |
| Recording fails | The Activity log shows which sound device was found. Replug the mic and try again. |
| **Microphone ✗** (recorded only silence) | Check the mic's mute switch or button, then talk closer and louder. |
| Nothing from the mic's speaker | Turn its volume up. Use **Play recording on computer** to tell a bad speaker from a bad mic. |
| A new mic plugged in shows nothing | Give it a second or two: the app retries the connection every second. Then try **Start this mic's test over**. |

## Document scanners

| Problem | Fix |
|---|---|
| "Scanner isn't found" | Make sure it's switched on, then click **Look for scanner again**. Check that `scanimage -L` lists it. |
| Streaks flagged on every page | Clean the scanner glass and rollers, then scan a blank white sheet again. |
| Pages come out blank | Check you picked the right side under **Sides / feeder**, and that pages are loaded face the right way. |

## Signature pads

| Problem | Fix |
|---|---|
| Pad isn't listed | Replug it. Check `lsusb` shows an FTDI device (`0403:6001`). |
| Pad listed but nothing draws | Log out and back in so the `dialout` group and udev rule take effect. |
| Random lines or a jumpy dot | The pad is being read at the wrong speed. Unplug and replug it. If that doesn't help, [open an issue](https://github.com/ChewyVKB/Bench-Tested/issues) with the model number from the label on the back. |
| Drawing is upside down | Tick **Upside down**. It's remembered for that model. |
| An edge never gets ticked | Run the pen slowly right off that edge. If it still won't register, that part of the digitizer may be faulty. |

## Cameras and conference hubs

| Problem | Fix |
|---|---|
| Listed but no picture | Try another resolution in the drop-down, or replug it. Close any other app using the camera (browser tabs, Zoom, Teams). |
| Meeting Owl and similar show nothing | Some conference cameras need first-time setup in their own app first. |
| **Speaker → mic** check fails | Make sure the device isn't muted and its volume is up, then try again in a quiet room. |
| Audio section is greyed out | The device has no mic or speaker, which is normal for plain webcams. |

## Collecting details for a bug report

Run these commands with the device plugged in, and include the output in your [issue](https://github.com/ChewyVKB/Bench-Tested/issues):

```bash
lsusb
bench-tested 2>&1 | tee ~/bench-tested-log.txt   # reproduce the problem, then close the app
```

Also copy the relevant lines from the **Activity** log.
