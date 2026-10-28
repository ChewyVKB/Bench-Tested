"""Test certificates: a running record per device (every check and Activity line), the asset
tag (typed, or scanned with a designated "asset tag scanner"), and a PDF certificate plus a
register (CSV) line for every unit - pass, fail or incomplete."""

import csv
import hashlib
import socket

from .common import *  # noqa: F401,F403
from .gtkutil import GdkPixbuf, GLib, Gtk, dim, lbl

try:
    from . import certpdf
except Exception:  # ReportLab missing
    certpdf = None

SOFTWARE = "Bench Tested 2.1"
DEFAULT_DIR = os.path.expanduser("~/Documents/Bench Tested/Certificates")
LOGO_PATH = os.path.join(DATA_DIR, "company-logo.png")
AUTO_CLOSE_SECS = 10          # unplugged this long without a certificate -> certificate made for it

TYPE_NAMES = {"printer": "Label printer", "scanner": "Barcode scanner", "mic": "Dictation microphone",
              "docscan": "Document scanner", "sigpad": "Signature pad", "camera": "Camera / conference device"}

# Checks only a person can judge. (name, needed for a PASS)
MANUAL_CHECKS = {
    "printer": [("Test label printed clearly", True), ("Printhead check band has no gaps", True)],
    "scanner": [],
    "mic": [("Every physical button registered", True)],
    "docscan": [("Pages fed without jams or skew", True)],
    "sigpad": [("Pad's own screen shows the ink (LCD models)", False)],
    "camera": [("Picture is clear and in focus", True)],
}


def usb_extra(d):
    """Extra identification straight from USB."""
    sd = d["sysdir"]
    rev = read_file(sd + "/bcdDevice")
    speed = read_file(sd + "/speed")
    out = []
    if d.get("manufacturer"):
        out.append(("Manufacturer (reported)", d["manufacturer"]))
    if rev:
        # bcdDevice: the release number the device reports over USB (often, not always, its firmware)
        out.append(("USB device release", f"{int(rev[:2], 16)}.{rev[2:]}" if len(rev) == 4 else rev))
    if speed:
        out.append(("USB speed", {"1.5": "Low speed", "12": "Full speed (USB 1.1)", "480": "High speed (USB 2.0)",
                                  "5000": "SuperSpeed (USB 3)"}.get(speed, f"{speed} Mb/s")))
    return out


class CertMixin:
    """Certificate bar, settings and PDF creation; mixed into the main window."""

    def cert_init(self):
        self.cert_recs = {}
        c = self.cfg.setdefault("cert", {})
        c.setdefault("tech", "Austin Pizano")
        c.setdefault("company", "")
        c.setdefault("company_line", "")
        c.setdefault("folder", DEFAULT_DIR)
        c.setdefault("prefix", "BT")
        c.setdefault("auto_close", True)
        c.setdefault("open_after", True)

    @property
    def ccfg(self):
        return self.cfg["cert"]

    # ---------------------------------------------------------- records
    def cert_rec(self, key, create=True):
        r = self.cert_recs.get(key)
        if r is None and create:
            r = {"key": key, "started": None, "events": [], "marks": {}, "asset": "", "plug": None,
                 "gone_since": None, "manual": {}, "notes": "", "result": "auto",
                 "files": [], "images": [], "last_cert": None}
            self.cert_recs[key] = r
        return r

    def _touch(self, r):
        if r["started"] is None:
            r["started"] = datetime.datetime.now()

    def cert_key_for_log(self, prefix, text):
        """Which device an Activity line is about."""
        key = getattr(self._tl, "dev_key", None)
        if key:
            return key
        m = re.match(r"^\[(.+?)\]", (prefix + text).strip())
        if m:
            for d in self.devices:
                if short_name(d) == m.group(1):
                    return d["key"]
        return None

    def cert_note(self, prefix, text):
        key = self.cert_key_for_log(prefix, text)
        if not key or key == self.ccfg.get("asset_scanner"):
            return
        r = self.cert_rec(key)
        self._touch(r)
        line = re.sub(r"^\[.+?\]\s*", "", (prefix + text).strip())
        r["events"].append((datetime.datetime.now(), line))
        for p in re.findall(r"(/[^\n]*?/Pictures/Bench Tested/[^\n]+?\.(?:png|svg|jpg))", text):
            if p not in r["files"]:
                r["files"].append(p)
        if key == self.selected_key:
            GLib.idle_add(self.cert_bar_refresh)

    def cert_mark(self, d, name, status, detail=""):
        """Record a check result for a device: status 'pass' | 'fail' | 'info'."""
        if not d:
            return
        r = self.cert_rec(d["key"])
        self._touch(r)
        r["marks"][name] = (status, detail, datetime.datetime.now())
        if d["key"] == self.selected_key:
            GLib.idle_add(self.cert_bar_refresh)

    def cert_snapshot_leaving(self, devs):
        """Called before the device pages forget an unplugged (or swapped) unit: keep a copy
        of its results so a certificate can still be made from them."""
        present = {d["key"]: d for d in devs}
        for key, r in self.cert_recs.items():
            old = r.get("dev")
            if not old or not self.cert_touched(r):
                continue
            d = present.get(key)
            if d is None or (d.get("plug") != old.get("plug") and not d["serial"]):
                try:
                    r["snap"] = self.cert_collect(old, r)
                except Exception:
                    pass

    def cert_devices_changed(self, devs):
        """A device whose key is its USB port (no serial number) and that got replugged is
        a new unit: close the old record first."""
        now = time.time()
        present = {d["key"]: d for d in devs}
        for key, r in list(self.cert_recs.items()):
            d = present.get(key)
            if d is None:
                r["gone_since"] = r["gone_since"] or now
                continue
            r["gone_since"] = None
            if d["serial"]:
                r.pop("snap", None)                # the same unit came back
            if r["plug"] and r["plug"] != d.get("plug") and not d["serial"]:
                self.cert_auto_close(key, d=r.get("dev"))
                continue
            r["plug"], r["dev"] = d.get("plug"), dict(d)
        for key, d in present.items():
            r = self.cert_rec(key)
            r["plug"], r["dev"] = d.get("plug"), dict(d)

    def cert_tick(self):
        """Every second: certify units that were tested and then unplugged."""
        now = time.time()
        for key, r in list(self.cert_recs.items()):
            if r["gone_since"] and now - r["gone_since"] > AUTO_CLOSE_SECS:
                self.cert_auto_close(key)

    def cert_touched(self, r):
        return bool(r["events"] or r["marks"])

    def cert_auto_close(self, key, d=None):
        r = self.cert_recs.pop(key, None)
        if not r or not self.cert_touched(r) or key == self.ccfg.get("asset_scanner"):
            return
        d = d or r.get("dev")
        if not d or not self.ccfg.get("auto_close", True):
            return
        r["auto"] = True
        self.cert_create(d, r, quiet=True)

    # ---------------------------------------------------------- asset tags
    def cert_wants_scan(self):
        """True while the Asset tag box has the cursor: then any scanner fills it in."""
        try:
            return bool(self.cb_tag.has_focus() and self.cb_tag.get_visible() and self.current())
        except Exception:
            return False

    def cert_asset_scanned(self, text):
        """A scan from the asset-tag scanner: fill in the tag of the device being tested."""
        text = text.strip()
        d = self.current()
        if not d or d["key"] == self.ccfg.get("asset_scanner"):
            self.log(f"Asset tag {text} scanned - pick the device it belongs to first, then scan again.")
            return
        r = self.cert_rec(d["key"])
        self._touch(r)
        r["asset"] = text
        self.log(f"[{short_name(d)}] Asset tag {text} scanned")
        self.cert_bar_refresh()
        self.cert_release_focus(d)              # the next scans are test scans again

    def cert_release_focus(self, d):
        if not self.cb_tag.has_focus():
            return
        if d["category"] == "scanner":
            self.b_entry.grab_focus()           # the scanner page's own scan box
        else:
            self.set_focus(None)

    def cert_focus_tag(self):
        """Put the cursor in the Asset tag box when it's still empty, so scanning the tag
        is the first thing that happens - not on scanner pages, where scans are tests."""
        d = self.current()
        if not d or d["category"] == "scanner" or d["key"] == self.ccfg.get("asset_scanner"):
            return False
        if not self.cert_rec(d["key"])["asset"]:
            self.cb_tag.grab_focus()
        return False

    def cert_set_asset_scanner(self, d, on):
        if on:
            self.ccfg["asset_scanner"] = d["key"]
            self.cert_recs.pop(d["key"], None)
            self.log(f"[{short_name(d)}] is now the asset tag scanner. While Bench Tested is the active "
                     "window, its scans fill in the asset tag of the device you're testing.")
        elif self.ccfg.get("asset_scanner") == d["key"]:
            self.ccfg.pop("asset_scanner")
            self.log(f"[{short_name(d)}] is a normal scanner again.")
        save_config(self.cfg)
        self.cert_bar_refresh()

    # ---------------------------------------------------------- what goes on the certificate
    def cert_collect(self, d, r):
        """(device fields, details, automatic checks, required check names, signature, images)."""
        cat = d["category"]
        device = [("Device type", TYPE_NAMES.get(cat, cat)), ("Make", d["brand"]),
                  ("Model", d["product"]), ("Serial number", d["serial"]),
                  ("USB ID", f"{d['vid']}:{d['pid']}")]
        details, checks, required, sig, images = list(usb_extra(d)), [], [], None, []

        def add(name, ok, detail="", need=True):
            st = "pass" if ok else ("fail" if ok is False else "skip")
            checks.append((name, st, detail))
            if need:
                required.append(name)

        marks = dict(r["marks"])
        try:
            fn = getattr(self, f"_cert_{cat}")
        except AttributeError:
            fn = None
        if fn:
            sig, images = fn(d, r, device, details, add, marks)
        for name, (status, detail, t) in marks.items():          # anything recorded along the way
            when = t.strftime('%I:%M:%S\u00a0%p').lstrip('0')      # keep "PM" with the time
            checks.append((name, status, f"{detail}  ·  {when}" if detail else when))
        return device, details, checks, required, sig, images

    def _cert_printer(self, d, r, device, details, add, marks):
        o = self.opts(d)
        if d["brand"] == "Zebra":
            z = o.get("zinfo")
            if z:
                W, H, D, info = z
                g = lambda k: (info.get(k) or "").replace("<-", "").strip()        # noqa: E731
                if g("SERIAL NUMBER"):
                    device[3] = ("Serial number", g("SERIAL NUMBER"))
                if W and H and D:
                    details.append(("Label size (detected)", f"{W / D:.2f} x {H / D:.2f} in"))
                    details.append(("Resolution", f"{D} dpi"))
                # all straight from the printer's ^HH configuration reply; only what it reports
                for k, label in (("FIRMWARE", "Firmware"), ("TOTAL USAGE", "Total usage (odometer)"),
                                 ("HEAD USAGE", "Head usage (since printhead replaced)"),
                                 ("LAST CLEANED", "Printhead last cleaned at"),
                                 ("MEDIA TYPE", "Media type"), ("SENSOR TYPE", "Sensor type"),
                                 ("PRINT MODE", "Print mode"), ("DARKNESS", "Darkness"),
                                 ("PRINT SPEED", "Print speed"), ("ZPL MODE", "ZPL mode")):
                    if g(k):
                        details.append((label, g(k)))
            details.append(("Printer language (set in Bench Tested)", o.get("lang", "ZPL")))
        else:
            from .printers import parse_dim_id, dymo_item_name
            dim = parse_dim_id(o.get("dsize"))
            if dim:
                details.append(("Labels loaded (picked by technician)", dymo_item_name(*dim) or f"{dim[0] / 72:.2f} x {dim[1] / 72:.2f} in"))
        sent = marks.pop("Test label sent", None)
        add("Test label sent", (sent[0] == "pass") if sent else None, sent[1] if sent else "Not printed")
        return None, []

    def _cert_scanner(self, d, r, device, details, add, marks):
        from .barcode import CODE_BY_ID, MODE_TEXT, TEST_CODES, scanner_mode
        info = self.scan_info(d["key"])
        details.append(("Connection", MODE_TEXT.get(scanner_mode(d), "?").split(" - ")[0]))
        hits = info["hits"]
        n1 = len([c for c in hits if CODE_BY_ID[c][2] == "1D"])
        n2 = len([c for c in hits if CODE_BY_ID[c][2] == "2D"])
        details.append(("Reads", "1D and 2D barcodes" if n2 else "1D barcodes" if n1 else "Nothing read"))
        details.append(("Total scans", str(len(info["log"]))))
        add("Reads barcodes", True if hits else (False if info["log"] else None),
            f"{n1} of 4 1D and {n2} of 3 2D test codes read")
        for cid, name, kind, _data, _z in TEST_CODES:
            add(f"{kind}: {name}", True if cid in hits else None, "Read" if cid in hits else "", need=False)
        return None, []

    def _cert_mic(self, d, r, device, details, add, marks):
        from .mic import button_name
        s = self.mic_info(d)
        a = s.get("audio")
        if a is not None:
            details.append(("Microphone found", "Yes" if a.has_mic else "No"))
            details.append(("Speaker found", "Yes" if a.has_speaker else "No"))
        add("Microphone records", None if s["peak"] is None else s["peak"] > -45,
            "" if s["peak"] is None else f"Peak level {s['peak']:.0f} dB")
        if a is None or a.has_speaker:
            add("Speaker plays the recording", s["speaker"],
                "" if s["speaker"] is None else "Confirmed by technician")
        btns = sorted(c for c in s["buttons"])
        add("Buttons", True if btns else None,
            f"{len(btns)} working: " + ", ".join(button_name(c) for c in btns) if btns else "None pressed")
        if d.get("trackball"):
            from .inputdev import BTN_LEFT, BTN_RIGHT
            ball = s["ball"]
            parts = [n for k, n in (("move", "rolls"), (BTN_LEFT, "left click"), (BTN_RIGHT, "right click"))
                     if k in ball]
            add("Trackball", True if len(parts) == 3 else (None if not parts else False),
                ", ".join(parts) if parts else "Not tested")
        return None, []

    def _cert_docscan(self, d, r, device, details, add, marks):
        s = self.doc_info(d)
        if s.get("sane"):
            details.append(("Scanner (SANE)", s["sane"]))
        args = s.get("args") or []
        for opt, label in (("--source", "Sides / feeder"), ("--mode", "Color"), ("--resolution", "Resolution")):
            if opt in args:
                details.append((label, args[args.index(opt) + 1] + (" dpi" if opt == "--resolution" else "")))
        pages = s["pages"]
        blank = [i + 1 for i, p in enumerate(pages) if p[2] and p[2]["blank"]]
        streak = [i + 1 for i, p in enumerate(pages) if p[2] and p[2]["streaks"]]
        add("Feeds and scans pages", True if pages else None, f"{len(pages)} pages scanned" if pages else "")
        if pages:
            add("No streaks (dirty glass / bad sensor)", not streak,
                f"Possible streaks on page {', '.join(map(str, streak))}" if streak else "None found")
            checks_blank = f"Blank: page {', '.join(map(str, blank))}" if blank else "No blank pages"
            add("Blank page check", True, checks_blank, need=False)
        images = [(f"Page {i + 1}", p[0]) for i, p in enumerate(pages[:3])]
        if s.get("dir"):
            r["files"] = [s["dir"]] + [f for f in r["files"] if f != s["dir"]]
        return None, images

    def _cert_sigpad(self, d, r, device, details, add, marks):
        from .sigpad import pad_is_hid, pad_model
        s = self.pad_info(d)
        details.append(("Connection", "USB HID" if pad_is_hid(d) else "Serial (USB adapter)"))
        prof = self.cfg.get("sigpad_profile", {}).get(d["serial"] or "?")
        if prof and not pad_is_hid(d):
            details.append(("Port settings", prof))
        add("Pen writes", True if s["points"] else None,
            f"{s['points']} points in {len(s['strokes'] or s.get('last_sig') or [])} stroke(s)" if s["points"] else "")
        cov = self.pad_coverage(s)
        for edge in ("left", "right", "top", "bottom"):
            add(f"Edge check: {edge}", True if cov.get(edge) else (False if s["points"] else None),
                "" if cov.get(edge) else ("Pen didn't reach this edge" if s["points"] else ""))
        strokes = s["strokes"] if any(s["strokes"]) else (s.get("last_sig") or [])
        sig = None
        if strokes:
            sig = {"strokes": strokes, "bounds": tuple(s["bounds"]),
                   "flip": bool(self.cfg.get("sigpad_flip", {}).get(pad_model(d)))}
        return sig, []

    def _cert_camera(self, d, r, device, details, add, marks):
        s = self.cam_info(d)
        res = s["results"]
        modes = s.get("modes") or []
        if modes:
            details.append(("Highest resolution (reported)", modes[0][0].split("  ")[0]))
            details.append(("Resolutions supported", str(len(modes))))
        if s.get("node"):
            details.append(("Video device", s["node"]))
        a = s.get("audio")
        if a:
            details.append(("Microphone", "Yes" if a.has_mic else "No"))
            details.append(("Speaker", "Yes" if a.has_speaker else "No"))
        if not d.get("audio_only"):
            add("Live picture", res.get("video"), "Frames received" if res.get("video") else "")
        if "ptz" in res or any(c[0] in ("pan_absolute", "tilt_absolute", "zoom_absolute")
                               for c in (s.get("ctrls") or [])):
            add("Pan / tilt / zoom", res.get("ptz"), "", need=False)
        if a and a.has_mic and a.has_speaker:
            add("Speaker → mic check", res.get("loop"), "Test tone heard by the device's own mic"
                if res.get("loop") else "")
        if a and a.has_mic:
            add("Microphone records", res.get("mic"), "", need=not (a.has_speaker))
        photos = [f for f in r["files"] if " - photo " in f]
        return None, [("Photo taken during the test", p) for p in photos[-2:]]

    # ---------------------------------------------------------- result
    def cert_result(self, d, r, checks, required):
        manual = r["manual"]
        statuses = {n: st for n, st, _ in checks}
        for name, need in MANUAL_CHECKS.get(d["category"], []):
            v = manual.get(name)
            statuses[name] = "pass" if v is True else "fail" if v is False else "skip"
            if need:
                required = required + [name]
        if any(st == "fail" for st in statuses.values()):
            return "FAIL"
        if required and all(statuses.get(n) == "pass" for n in required):
            return "PASS"
        return "INCOMPLETE"

    # ---------------------------------------------------------- the certificate
    def cert_next_id(self):
        today = datetime.date.today().strftime("%Y%m%d")
        c = self.ccfg.setdefault("counter", {"date": today, "n": 0})
        if c["date"] != today:
            c.update(date=today, n=0)
        c["n"] += 1
        save_config(self.cfg)
        return f"{self.ccfg.get('prefix') or 'BT'}-{today}-{c['n']:03d}"

    def cert_create(self, d, r, quiet=False):
        """Build the PDF and add a line to the register. Returns the PDF path or None."""
        if certpdf is None:
            self.log("Certificates need ReportLab: run ./install.sh again (or: sudo apt install python3-reportlab).")
            return None
        device, details, checks, required, sig, images = r.get("snap") or self.cert_collect(d, r)
        checks = list(checks)
        details = list(details)
        auto_result = self.cert_result(d, r, checks, required)
        result = auto_result if r["result"] == "auto" else r["result"]
        for name, _need in MANUAL_CHECKS.get(d["category"], []):
            v = r["manual"].get(name)
            checks.append((name, "pass" if v is True else "fail" if v is False else "skip",
                           "Confirmed by technician" if v is not None else ""))
        now = datetime.datetime.now()
        cert_id = self.cert_next_id()
        note = ""
        if r.get("auto"):
            note = "Created automatically when the device was unplugged"
        elif result != auto_result:
            note = f"Set by technician (automatic result: {auto_result})"
        folder = os.path.join(os.path.expanduser(self.ccfg.get("folder") or DEFAULT_DIR),
                              now.strftime("%Y"), now.strftime("%m"))
        os.makedirs(folder, exist_ok=True)
        safe = lambda t: re.sub(r"[^A-Za-z0-9._-]+", "-", t).strip("-")        # noqa: E731
        name = "_".join(x for x in (cert_id, safe(r["asset"]) or "NO-TAG", safe(d["product"])[:40]) if x)
        path = os.path.join(folder, name + ".pdf")
        data = {
            "company": self.ccfg.get("company") or "", "company_line": self.ccfg.get("company_line") or "",
            "logo": LOGO_PATH if os.path.exists(LOGO_PATH) else None,
            "title": "Device Test Certificate", "subtitle": "Functional test record",
            "cert_id": cert_id, "result": result, "result_note": note, "asset_tag": r["asset"],
            "device": device, "details": details, "checks": checks, "signature": sig,
            "images": images, "files": r["files"], "activity": r["events"],
            "tech": self.ccfg.get("tech") or "", "station": socket.gethostname(),
            "started": r["started"] or now, "completed": now, "software": SOFTWARE,
            "notes": r["notes"],
        }
        try:
            certpdf.build_pdf(path, data)
        except Exception as e:
            self.log(f"Couldn't create the certificate: {e}")
            return None
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.cert_register(data, d, path, digest)
        r["last_cert"] = (cert_id, path, result)
        self.cfg.setdefault("cert_last", {})[d["key"]] = [cert_id, path, result]
        save_config(self.cfg)
        self.log(f"[{short_name(d)}] Certificate {cert_id} created ({result}"
                 f"{', no asset tag' if not r['asset'] else ''}): {path}")
        if not quiet and self.ccfg.get("open_after", True):
            run(["xdg-open", path], timeout=5)
        return path

    def cert_register(self, data, d, path, digest):
        """One line per certificate in a CSV that opens in Google Sheets / Excel."""
        reg = os.path.join(os.path.expanduser(self.ccfg.get("folder") or DEFAULT_DIR),
                           "certificate-register.csv")
        new = not os.path.exists(reg)
        dev = dict(data["device"])
        passed = sum(1 for c in data["checks"] if c[1] == "pass")
        failed = sum(1 for c in data["checks"] if c[1] == "fail")
        with open(reg, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["Certificate", "Completed", "Asset tag", "Result", "Device type", "Make", "Model",
                            "Serial number", "USB ID", "Checks passed", "Checks failed", "Technician",
                            "Test station", "Started", "Notes", "PDF", "SHA-256"])
            w.writerow([data["cert_id"], data["completed"].strftime("%Y-%m-%d %H:%M:%S"), data["asset_tag"],
                        data["result"], dev.get("Device type"), dev.get("Make"), dev.get("Model"),
                        dev.get("Serial number") or "", dev.get("USB ID"), passed, failed, data["tech"],
                        data["station"], data["started"].strftime("%Y-%m-%d %H:%M:%S"),
                        (data["notes"] + ("; " if data["notes"] and data["result_note"] else "") +
                         data["result_note"]), path, digest])

    # ---------------------------------------------------------- UI: the bar under each device page
    def build_cert_bar(self):
        bar = Gtk.Box(spacing=8)
        bar.set_border_width(8)
        self.cb_tag_lbl = lbl("<b>Asset tag</b>", markup=True)
        bar.pack_start(self.cb_tag_lbl, False, False, 0)
        self.cb_tag = Gtk.Entry()
        self.cb_tag.set_placeholder_text("Click here, then scan or type")
        self.cb_tag.set_tooltip_text("While the cursor is in this box, a scan from any scanner fills it in")
        self.cb_tag.set_width_chars(22)
        self.cb_tag.connect("activate", lambda *_: self.current() and self.cert_release_focus(self.current()))
        self.cb_tag.connect("changed", self.on_cert_tag_typed)
        bar.pack_start(self.cb_tag, False, False, 0)
        self.cb_status = dim(lbl("", wrap=True))
        bar.pack_start(self.cb_status, True, True, 0)
        self.cb_asset = Gtk.CheckButton(label="Use as asset tag scanner")
        self.cb_asset.set_tooltip_text("Scans from this scanner fill in the asset tag of the device you're "
                                       "testing, instead of counting as test scans")
        self.cb_asset.connect("toggled", self.on_cert_asset_toggle)
        bar.pack_start(self.cb_asset, False, False, 0)
        self.cb_open = Gtk.Button(label="Open last")
        self.cb_open.set_tooltip_text("Open this device's last certificate")
        self.cb_open.connect("clicked", lambda *_: self.cert_open_last())
        bar.pack_start(self.cb_open, False, False, 0)
        self.cb_make = Gtk.Button(label="Create certificate…")
        self.cb_make.get_style_context().add_class("suggested-action")
        self.cb_make.connect("clicked", lambda *_: self.cert_dialog())
        bar.pack_start(self.cb_make, False, False, 0)
        self.cert_bar = bar
        return bar

    def on_cert_tag_typed(self, entry):
        if self._loading:
            return
        d = self.current()
        if d:
            r = self.cert_rec(d["key"])
            r["asset"] = entry.get_text().strip()

    def on_cert_asset_toggle(self, w):
        if self._loading:
            return
        d = self.current()
        if d:
            self.cert_set_asset_scanner(d, w.get_active())

    def cert_open_last(self):
        d = self.current()
        last = d and (self.cert_rec(d["key"])["last_cert"] or self.cfg.get("cert_last", {}).get(d["key"]))
        if last and os.path.exists(last[1]):
            run(["xdg-open", last[1]], timeout=5)

    def cert_bar_refresh(self):
        d = self.current()
        self.cert_bar.set_visible(bool(d))
        if not d:
            return False
        is_asset = d["key"] == self.ccfg.get("asset_scanner")
        self._loading = True
        self.cb_asset.set_visible(d["category"] == "scanner")
        self.cb_asset.set_active(is_asset)
        r = self.cert_rec(d["key"])
        if self.cb_tag.get_text().strip() != r["asset"]:
            self.cb_tag.set_text(r["asset"])
        self._loading = False
        for w in (self.cb_tag_lbl, self.cb_tag, self.cb_make):
            w.set_visible(not is_asset)
        last = r["last_cert"] or self.cfg.get("cert_last", {}).get(d["key"])
        self.cb_open.set_visible(bool(last) and not is_asset)
        if is_asset:
            self.cb_status.set_text("This is the asset tag scanner: while Bench Tested is the active window, "
                                    "its scans fill in the asset tag of the device you're testing.")
            return False
        _dev, _det, checks, required, _s, _i = self.cert_collect(d, r)
        p = sum(1 for c in checks if c[1] == "pass")
        f = sum(1 for c in checks if c[1] == "fail")
        bits = [f"{p} passed" + (f", {f} failed" if f else "")] if (p or f) else ["No tests yet"]
        if last:
            bits.append(f"last certificate {last[0]} ({last[2]})")
        self.cb_status.set_text("  ·  ".join(bits))
        return False

    # ---------------------------------------------------------- UI: create certificate
    def cert_dialog(self):
        d = self.current()
        if not d:
            return
        r = self.cert_rec(d["key"])
        dlg = Gtk.Dialog(title=f"Certificate - {display_name(d)}", transient_for=self, modal=True)
        dlg.set_default_size(560, -1)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Create certificate", Gtk.ResponseType.OK)
        dlg.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_border_width(14)
        box.add(lbl(f"<b>{GLib.markup_escape_text(display_name(d))}</b>   SN {GLib.markup_escape_text(d['serial'] or 'not reported')}",
                    markup=True))
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        tag = Gtk.Entry(text=r["asset"])
        tag.set_placeholder_text("Scan or type the asset tag")
        grid.attach(lbl("Asset tag:"), 0, 0, 1, 1)
        grid.attach(tag, 1, 0, 1, 1)
        _dev, _det, checks, required, _s, _i = self.cert_collect(d, r)
        row = 1
        auto_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        icon = {"pass": "✓", "fail": "✗", "info": "•", "skip": "○"}
        for name, st, detail in checks:
            auto_box.add(lbl(f"{icon.get(st, '•')} {name}" + (f"  -  {detail}" if detail else ""), wrap=True))
        if not checks:
            auto_box.add(dim(lbl("No tests have been run on this device yet.")))
        grid.attach(lbl("<b>Recorded</b>", markup=True), 0, row, 1, 1)
        grid.attach(auto_box, 1, row, 1, 1)
        row += 1
        manual_w = {}
        for name, need in MANUAL_CHECKS.get(d["category"], []):
            c = Gtk.ComboBoxText()
            for vid, text in (("none", "Not checked"), ("yes", "Yes"), ("no", "No")):
                c.append(vid, text)
            v = r["manual"].get(name)
            c.set_active_id("yes" if v is True else "no" if v is False else "none")
            grid.attach(lbl(name + ("" if need else " (optional)") + ":", wrap=True), 0, row, 1, 1)
            grid.attach(c, 1, row, 1, 1)
            manual_w[name] = c
            row += 1
        notes = Gtk.Entry(text=r["notes"])
        notes.set_placeholder_text("Optional - e.g. missing power supply")
        grid.attach(lbl("Notes:"), 0, row, 1, 1)
        grid.attach(notes, 1, row, 1, 1)
        row += 1
        result = Gtk.ComboBoxText()
        res_lbl = lbl("")

        def update_auto(*_a):
            for name, c in manual_w.items():
                v = c.get_active_id()
                r["manual"][name] = True if v == "yes" else False if v == "no" else None
            auto = self.cert_result(d, r, checks, required)
            cur = result.get_active_id() or "auto"
            result.remove_all()
            result.append("auto", f"Automatic: {auto}")
            for x in ("PASS", "FAIL", "INCOMPLETE"):
                result.append(x, x.title())
            result.set_active_id(cur)
        for c in manual_w.values():
            c.connect("changed", update_auto)
        update_auto()
        result.set_active_id(r["result"])
        grid.attach(lbl("<b>Result:</b>", markup=True), 0, row, 1, 1)
        grid.attach(result, 1, row, 1, 1)
        box.add(grid)
        box.add(res_lbl)
        box.add(dim(lbl(f"Technician: {self.ccfg.get('tech') or '(set in Certificate settings)'}", wrap=True)))
        dlg.show_all()
        resp = dlg.run()
        if resp == Gtk.ResponseType.OK:
            r["asset"] = tag.get_text().strip()
            r["notes"] = notes.get_text().strip()
            r["result"] = result.get_active_id() or "auto"
            dlg.destroy()
            if not r["asset"]:
                ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.YES_NO, text="No asset tag entered")
                ask.format_secondary_text("Create the certificate without an asset tag?")
                go = ask.run() == Gtk.ResponseType.YES
                ask.destroy()
                if not go:
                    return
            path = self.cert_create(d, r)
            if path:
                last = r["last_cert"]
                del self.cert_recs[d["key"]]
                fresh = self.cert_rec(d["key"])           # a re-test starts a new record
                fresh["last_cert"], fresh["plug"], fresh["dev"] = last, d.get("plug"), dict(d)
                self.cert_bar_refresh()
        else:
            dlg.destroy()

    # ---------------------------------------------------------- UI: settings
    def cert_settings_dialog(self):
        c = self.ccfg
        dlg = Gtk.Dialog(title="Certificate settings", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_border_width(14)
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        fields = {}
        for i, (key, label, hint) in enumerate((
                ("company", "Company name", ""), ("company_line", "Line under the name", "e.g. R2v3 Certified Facility"),
                ("tech", "Technician", ""), ("prefix", "Certificate number prefix", "e.g. BT"))):
            e = Gtk.Entry(text=c.get(key) or "")
            e.set_placeholder_text(hint)
            e.set_width_chars(34)
            grid.attach(lbl(label + ":"), 0, i, 1, 1)
            grid.attach(e, 1, i, 1, 1)
            fields[key] = e
        row = 4
        logo_img = Gtk.Image()
        if os.path.exists(LOGO_PATH):
            logo_img.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(LOGO_PATH, 220, 60, True))
        logo_btn = Gtk.Button(label="Choose logo…")
        picked = {"path": None}

        def pick_logo(*_a):
            fc = Gtk.FileChooserDialog(title="Company logo", transient_for=dlg, action=Gtk.FileChooserAction.OPEN)
            fc.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
            flt = Gtk.FileFilter()
            flt.set_name("Images (PNG, JPG, SVG)")
            for p in ("*.png", "*.jpg", "*.jpeg", "*.svg", "*.PNG", "*.JPG", "*.SVG"):
                flt.add_pattern(p)
            fc.add_filter(flt)
            if fc.run() == Gtk.ResponseType.OK:
                picked["path"] = fc.get_filename()
                try:
                    logo_img.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(picked["path"], 220, 60, True))
                except Exception:
                    pass
            fc.destroy()
        logo_btn.connect("clicked", pick_logo)
        lb = Gtk.Box(spacing=8)
        lb.pack_start(logo_btn, False, False, 0)
        lb.pack_start(logo_img, False, False, 0)
        grid.attach(lbl("Logo:"), 0, row, 1, 1)
        grid.attach(lb, 1, row, 1, 1)
        row += 1
        folder = Gtk.FileChooserButton(title="Certificates folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
        os.makedirs(os.path.expanduser(c.get("folder") or DEFAULT_DIR), exist_ok=True)
        folder.set_filename(os.path.expanduser(c.get("folder") or DEFAULT_DIR))
        grid.attach(lbl("Save certificates in:"), 0, row, 1, 1)
        grid.attach(folder, 1, row, 1, 1)
        row += 1
        auto = Gtk.CheckButton(label="Create a certificate automatically when a tested device is unplugged "
                                     "without one")
        auto.set_active(c.get("auto_close", True))
        grid.attach(auto, 0, row, 2, 1)
        row += 1
        openafter = Gtk.CheckButton(label="Open each certificate after creating it")
        openafter.set_active(c.get("open_after", True))
        grid.attach(openafter, 0, row, 2, 1)
        box.add(grid)
        ob = Gtk.Button(label="Open certificates folder")
        ob.connect("clicked", lambda *_: self.cert_open_folder())
        box.add(ob)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            for k, e in fields.items():
                c[k] = e.get_text().strip()
            c["folder"] = folder.get_filename() or c.get("folder")
            c["auto_close"] = auto.get_active()
            c["open_after"] = openafter.get_active()
            if picked["path"]:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(picked["path"], 1200, 400, True)
                    os.makedirs(DATA_DIR, exist_ok=True)
                    pb.savev(LOGO_PATH, "png", [], [])
                    self.log("Company logo saved for certificates.")
                except Exception as e:
                    self.log(f"Couldn't use that logo: {e}")
            save_config(self.cfg)
        dlg.destroy()

    def cert_open_folder(self):
        f = os.path.expanduser(self.ccfg.get("folder") or DEFAULT_DIR)
        os.makedirs(f, exist_ok=True)
        run(["xdg-open", f], timeout=5)
