"""Document scanners (Fujitsu fi-series and other SANE scanners): scan the stack in the
feeder, show every page as it comes in, flag blank pages and streaks, and keep the
scans as timestamped proof."""

import subprocess as _sp

from .common import *  # noqa: F401,F403
from .gtkutil import GdkPixbuf, GLib, Gtk, combo, dim, lbl

PROOF_DIR = os.path.expanduser("~/Pictures/Bench Tested")
NETWORK_BACKENDS = ("escl", "airscan", "hpaio", "net", "v4l", "pixma:", "epsonds:net")


# ------------------------------------------------------------------ SANE helpers

def sane_devices():
    """[(name, description)] of USB scanners SANE can see (network ones skipped)."""
    r = run(["scanimage", "-L"], timeout=90)
    out = []
    for m in re.finditer(r"device `([^']+)' is a (.+)", r.stdout):
        name, desc = m.group(1), m.group(2).strip()
        if name.startswith(NETWORK_BACKENDS) or "ip=" in desc:
            continue
        out.append((name, desc))
    return out


def match_sane(d, devices, all_docscans):
    """Which SANE device is this USB scanner?"""
    model = norm(d["product"])
    cands = [n for n, desc in devices if model and (model in norm(n.replace(":", " "))
                                                    or model in norm(desc))]
    if not cands:
        brand = d["vid"]
        cands = [n for n, _desc in devices if brand == "04c5" and n.startswith("fujitsu")] or \
                [n for n, _desc in devices]
    if d["serial"]:
        for n in cands:
            if d["serial"] in n:
                return n
    same = [x for x in all_docscans if norm(x["product"]) == model]
    idx = next((i for i, x in enumerate(same) if x["key"] == d["key"]), 0)
    return cands[idx] if idx < len(cands) else (cands[0] if cands else None)


def sane_options(name):
    """{'source': [...], 'mode': [...], 'resolution': [...]} with defaults, from scanimage -A."""
    r = run(["scanimage", "-d", name, "-A"], timeout=60)
    opts = {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s+--(source|mode|resolution)\s+(.+?)\s+\[(.+?)\]", line)
        if not m:
            continue
        key, vals, default = m.groups()
        if key == "resolution":
            nums = [int(v) for v in re.findall(r"\d+", vals)]
            if ".." in vals and len(nums) >= 2:
                lo, hi = nums[0], nums[1]
                choices = [v for v in (100, 150, 200, 300, 400, 600) if lo <= v <= hi]
            else:
                choices = sorted({v for v in nums if 50 <= v <= 1200})
            opts[key] = ([str(v) for v in choices], re.sub(r"\D", "", default))
        else:
            opts[key] = (vals.split("|"), default)
    return opts


def analyze_pixels(get_row, w, h):
    """Look at a scanned page. get_row(y) returns a row of brightness values (0-255).
    Returns {'blank': bool, 'streaks': [percent across], 'dark': fraction}."""
    if w < 20 or h < 20:
        return {"blank": False, "streaks": [], "dark": 0.0}
    x0, x1 = int(w * 0.04), int(w * 0.96)
    y0, y1 = int(h * 0.03), int(h * 0.97)
    cols = [0] * w
    total = 0
    for y in range(y0, y1):
        row = get_row(y)
        for x in range(x0, x1):
            if row[x] < 150:
                cols[x] += 1
                total += 1
    rows = y1 - y0
    dark = total / max(1, (x1 - x0) * rows)
    streaks = []
    if dark < 0.06:   # mostly white page: dark lines running top to bottom = dirty glass / bad sensor
        run_start = None
        for x in range(x0, x1 + 1):
            hit = x < x1 and cols[x] > rows * 0.6
            if hit and run_start is None:
                run_start = x
            elif not hit and run_start is not None:
                streaks.append(round(100 * ((run_start + x - 1) / 2) / w))
                run_start = None
    return {"blank": dark < 0.003 and not streaks, "streaks": streaks, "dark": dark}


def analyze_file(path):
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 500, -1, True)
    w, h, stride, n = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels()
    px = pb.get_pixels()
    g_off = 1 if n >= 3 else 0
    return analyze_pixels(lambda y: px[y * stride + g_off: y * stride + w * n: n], w, h)


def page_verdict(res):
    if res is None:
        return "?"
    if res["streaks"]:
        return "⚠ line at " + ", ".join(f"{p}%" for p in res["streaks"][:3]) + " across"
    if res["blank"]:
        return "blank page"
    return "✓ looks good"


class DocScanMixin:
    """Document-scanner page; mixed into the main window."""

    def docscan_init(self):
        self.doc_state = {}
        self.sane_cache = None          # list from scanimage -L
        self.sane_loading = False

    def doc_info(self, d):
        s = self.doc_state.get(d["key"])
        if s is None:
            s = {"sane": None, "opts": None, "pages": [], "dir": None, "proc": None}
            self.doc_state[d["key"]] = s
        return s

    def docscan_devices_changed(self, devs):
        docs = {d["key"]: d for d in devs if d["category"] == "docscan"}
        for key in list(self.doc_state):
            st = self.doc_state[key]
            if (key not in docs or st.get("plug") != docs[key].get("plug")) and st["proc"] is None:
                del self.doc_state[key]
        for key, d in docs.items():
            self.doc_info(d)["plug"] = d.get("plug")
        if any(d["category"] == "docscan" for d in devs) and not self.sane_loading:
            self.sane_cache = None
            self.doc_find_all()

    def doc_find_all(self):
        """Ask SANE which scanners it sees (slow: it also probes the network)."""
        self.sane_loading = True

        def _work():
            found = sane_devices()

            def _done():
                self.sane_cache = found
                self.sane_loading = False
                docs = [x for x in self.devices if x["category"] == "docscan"]
                for x in docs:
                    s = self.doc_info(x)
                    s["sane"] = match_sane(x, found, docs)
                d = self.current()
                if d and d["category"] == "docscan":
                    self.docscan_select(d)
                return False
            GLib.idle_add(_done)
        self.in_thread(_work)

    # ---------------------------------------------------------- page
    def build_docscan_page(self):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(16)
        outer.add(page)
        self.s_title = lbl()
        self.s_sub = dim(lbl(selectable=True))
        self.s_status = lbl(wrap=True)
        for w in (self.s_title, self.s_sub, self.s_status):
            page.pack_start(w, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.s_source = Gtk.ComboBoxText()
        self.s_mode = Gtk.ComboBoxText()
        self.s_res = Gtk.ComboBoxText()
        for r, (t, w) in enumerate((("Sides / feeder:", self.s_source), ("Color:", self.s_mode),
                                    ("Resolution (dpi):", self.s_res))):
            grid.attach(lbl(t), 0, r, 1, 1)
            grid.attach(w, 1, r, 1, 1)
            self.page_widgets.append(w)
        page.pack_start(grid, False, False, 0)
        page.pack_start(dim(lbl("Load a few pages in the feeder. Include one blank white sheet: on a "
                                "blank page, dark lines running top to bottom (dirty glass or a bad "
                                "sensor) are found automatically.", wrap=True)), False, False, 0)

        row = Gtk.Box(spacing=8)
        self.s_scan = Gtk.Button(label="Scan the feeder")
        self.s_scan.get_style_context().add_class("suggested-action")
        self.s_scan.set_size_request(-1, 40)
        self.s_scan.connect("clicked", lambda *_: self.doc_start_scan())
        row.pack_start(self.s_scan, True, True, 0)
        self.page_widgets.append(self.s_scan)
        self.s_stop = Gtk.Button(label="Stop")
        self.s_stop.connect("clicked", lambda *_: self.doc_stop())
        row.pack_start(self.s_stop, False, False, 0)
        self.s_open = Gtk.Button(label="Open scans folder")
        self.s_open.connect("clicked", lambda *_: self.doc_open_folder())
        row.pack_start(self.s_open, False, False, 0)
        self.s_retry = Gtk.Button(label="Look for scanner again")
        self.s_retry.connect("clicked", lambda *_: self.doc_find_all())
        row.pack_start(self.s_retry, False, False, 0)
        page.pack_start(row, False, False, 0)

        self.s_progress = lbl(markup=True, wrap=True)
        page.pack_start(self.s_progress, False, False, 0)
        self.s_flow = Gtk.FlowBox()
        self.s_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.s_flow.set_max_children_per_line(5)
        self.s_flow.set_column_spacing(10)
        self.s_flow.set_row_spacing(10)
        page.pack_start(self.s_flow, False, False, 0)
        return outer

    def docscan_select(self, d):
        s = self.doc_info(d)
        self.s_title.set_markup(f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>")
        self.s_sub.set_text(f"Serial {d['serial'] or '?'}  ·  USB id {d['vid']}:{d['pid']}")
        if self.sane_loading or self.sane_cache is None:
            self.s_status.set_text("Looking for the scanner… (this takes a few seconds)")
        elif not s["sane"] and d.get("no_linux_driver"):
            self.s_status.set_text(f"There's no Linux driver for this scanner ({d['no_linux_driver']}), "
                                   "so it can't scan from Ubuntu. It's listed so it still gets a certificate: "
                                   "test-scan it on a Windows or Mac computer, then confirm that in "
                                   "'Create certificate…' below.")
        elif not s["sane"]:
            self.s_status.set_text("Ubuntu's scanner support doesn't see this scanner. Check it's switched "
                                   "on, then click 'Look for scanner again'. If it still isn't found, "
                                   "it may need: sudo apt install sane-utils libsane1")
        else:
            self.s_status.set_text(f"✔ Ready  (scanner name: {s['sane']})")
            if s["opts"] is None:
                self.doc_load_opts(d)
        self.doc_fill_opts(d)
        busy = s["proc"] is not None
        self.s_stop.set_visible(busy)
        self.s_scan.set_sensitive(bool(s["sane"]) and not busy and d["key"] not in self.busy_keys)
        self.s_open.set_visible(bool(s["dir"]))
        self.doc_show_pages(d)

    def doc_load_opts(self, d):
        s = self.doc_info(d)
        s["opts"] = {}

        def _work():
            o = sane_options(s["sane"])

            def _done():
                s["opts"] = o
                if d["key"] == self.selected_key:
                    self.doc_fill_opts(d)
                return False
            GLib.idle_add(_done)
        self.in_thread(_work)

    def doc_fill_opts(self, d):
        o = self.doc_info(d)["opts"] or {}
        self._loading = True
        for widget, key, prefer in ((self.s_source, "source", "Duplex"), (self.s_mode, "mode", "Color"),
                                    (self.s_res, "resolution", "200")):
            widget.remove_all()
            vals, default = o.get(key, ([], None))
            for v in vals:
                widget.append(v, v)
            pick = next((v for v in vals if prefer.lower() in v.lower()), default)
            if vals:
                widget.set_active_id(pick if pick in vals else vals[0])
            widget.set_sensitive(bool(vals))
        self._loading = False

    # ---------------------------------------------------------- scanning
    def doc_start_scan(self):
        d = self.current()
        if not d or d["category"] != "docscan":
            return
        s = self.doc_info(d)
        if not s["sane"]:
            return
        args = ["scanimage", "-d", s["sane"], "--format=png"]
        for widget, opt in ((self.s_source, "--source"), (self.s_mode, "--mode"),
                            (self.s_res, "--resolution")):
            v = widget.get_active_id()
            if v:
                args += [opt, v]
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        safe = re.sub(r"[^A-Za-z0-9 ._-]+", "", f"{d['product']} {d['serial']}".strip())
        s["dir"] = os.path.join(PROOF_DIR, f"{safe} - {stamp}")
        os.makedirs(s["dir"], exist_ok=True)
        args.append(f"--batch={s['dir']}/page-%03d.png")
        s["pages"] = []
        s["args"] = args
        duplex = "duplex" in (self.s_source.get_active_id() or "").lower()
        self.task(d, self.doc_scan, d, args, duplex)
        self.docscan_select(d)

    def doc_scan(self, d, args, duplex):
        s = self.doc_info(d)
        self.log("Scanning… " + " ".join(shlex.quote(a) for a in args[3:]))
        try:
            proc = _sp.Popen(args, stdout=_sp.DEVNULL, stderr=_sp.PIPE, text=True, env=ENV_C)
        except OSError as e:
            self.log(f"Couldn't start scanning: {e}")
            return False
        s["proc"] = proc
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.docscan_select(d), False)[1])
        seen, errors = set(), []

        def collect():
            for f in sorted(glob.glob(os.path.join(s["dir"], "page-*.png"))):
                if f in seen:
                    continue
                if proc.poll() is None and f == sorted(glob.glob(os.path.join(s["dir"], "page-*.png")))[-1]:
                    continue    # still being written
                seen.add(f)
                n = len(seen)
                side = ("front" if n % 2 else "back") if duplex else ""
                sheet = (n + 1) // 2 if duplex else n
                label = f"Sheet {sheet} {side}".strip() if duplex else f"Page {n}"
                try:
                    res = analyze_file(f)
                except Exception:
                    res = None
                s["pages"].append((f, label, res))
                GLib.idle_add(lambda: (d["key"] == self.selected_key and self.doc_show_pages(d), False)[1])

        for line in proc.stderr:
            line = line.strip()
            if re.search(r"error|invalid|jam|fail|cover", line, re.I) and "out of documents" not in line:
                errors.append(line)
            collect()
        proc.wait()
        collect()
        s["proc"] = None
        n = len(s["pages"])
        flagged = [p for p in s["pages"] if p[2] and p[2]["streaks"]]
        if n == 0:
            msg = errors[-1] if errors else "No pages came through - is there paper in the feeder?"
            self.log("Nothing scanned: " + msg)
            ok = "✗ nothing scanned"
        else:
            self.log(f"Scanned {n} page{'s' if n != 1 else ''}. Saved in: {s['dir']}" +
                     (f"  ⚠ possible streaks on {len(flagged)} page(s)" if flagged else ""))
            for e in errors:
                self.log("Scanner said: " + e)
            ok = f"{'⚠' if flagged else '✓'} {n} pages scanned" + (" · possible streaks" if flagged else "")
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.docscan_select(d), False)[1])
        return ok

    def doc_stop(self):
        d = self.current()
        if d:
            proc = self.doc_info(d)["proc"]
            if proc and proc.poll() is None:
                proc.terminate()
                self.log("Stopping the scan…")

    def doc_open_folder(self):
        d = self.current()
        if d and self.doc_info(d)["dir"]:
            subprocess.Popen(["xdg-open", self.doc_info(d)["dir"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def doc_show_pages(self, d):
        s = self.doc_info(d)
        for child in self.s_flow.get_children():
            self.s_flow.remove(child)
        for path, label, res in s["pages"]:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            img = Gtk.Image()
            try:
                img.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 150, 200, True))
            except Exception:
                img.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            ev = Gtk.EventBox()
            ev.add(img)
            ev.connect("button-press-event", lambda *_a, p=path: subprocess.Popen(
                ["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            ev.set_tooltip_text("Click to open full size")
            card.pack_start(ev, False, False, 0)
            card.pack_start(lbl(f"<b>{label}</b>", markup=True, xalign=0.5), False, False, 0)
            card.pack_start(lbl(page_verdict(res), xalign=0.5), False, False, 0)
            self.s_flow.add(card)
        self.s_flow.show_all()
        n = len(s["pages"])
        if s["proc"] is not None:
            self.s_progress.set_markup(f"<b>Scanning…</b> {n} page{'s' if n != 1 else ''} so far")
        elif n:
            bad = [p for p in s["pages"] if p[2] and p[2]["streaks"]]
            self.s_progress.set_markup(
                f"<b>{n} page{'s' if n != 1 else ''} scanned.</b> " +
                ("Possible lines/streaks found - look at the flagged pages; cleaning the glass usually "
                 "fixes it." if bad else "No streaks found."))
        else:
            self.s_progress.set_text("")
