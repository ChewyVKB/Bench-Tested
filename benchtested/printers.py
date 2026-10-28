"""Label printers: Zebra (direct USB, ZPL/EPL) and Dymo LabelWriter (CUPS)."""

from .common import *  # noqa: F401,F403
from .gtkutil import Gtk, GLib, Pango, call_main, combo, lbl


STD_SIZES = [  # inches, width x length
    (4, 6), (4, 4), (4, 3), (4, 2.5), (4, 2), (4, 1), (3, 2), (3, 1),
    (2.25, 1.25), (2.25, 0.75), (2, 1), (1.5, 1),
]


def zebra_write(dev, data):
    fd = os.open(dev, os.O_WRONLY)
    try:
        os.write(fd, data.encode("latin-1", "replace"))
    finally:
        os.close(fd)


def zebra_query(dev, cmd, wait=5):
    """Send a command and collect the reply (Zebras answer over USB)."""
    if not (os.access(dev, os.R_OK) and os.access(dev, os.W_OK)):
        return ""
    try:
        fd = os.open(dev, os.O_RDWR)
    except OSError:
        return ""
    try:
        os.write(fd, cmd.encode())
        r = subprocess.run(["timeout", str(wait), "cat"], stdin=fd,
                           capture_output=True)
        out = r.stdout.decode("latin-1", "replace")
    except OSError:
        out = ""
    finally:
        os.close(fd)
    return out.replace("\x02", "").replace("\x03", "").replace("\r", "")


def parse_hh(resp):
    """Parse the ^HH configuration dump into {KEY: value}."""
    info = {}
    for line in resp.splitlines():
        m = re.match(r"^\s*(.+?)\s{2,}(\S.*?)\s*$", line)
        if m:
            info[m.group(2).strip().upper()] = m.group(1).strip()
    return info


def dpi_from_info(info, width):
    m = re.search(r"(\d+)\s*(?:/MM|DPMM)", info.get("RESOLUTION", ""), re.I)
    dpmm = int(m.group(1)) if m else 0
    return {8: 203, 12: 300, 24: 600}.get(dpmm, 300 if width >= 1100 else 203)


def snap_size(w, h, d):
    """Printer's measured length includes the gap: snap to a standard size."""
    wi, hi = w / d, h / d
    for sw, sh in STD_SIZES:
        if sw - 0.15 <= wi <= sw + 0.2 and sh - 0.05 <= hi <= sh + 0.55:
            return int(sw * d), int(sh * d), d
    return w, int(h * 0.94), d


def zebra_detect(dev):
    """Return (W, H, DPI, info) or None."""
    resp = zebra_query(dev, "^XA^HH^XZ", 5)
    if not resp.strip():
        return None
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "label-printer-test-last.txt"), "w") as f:
            f.write(resp)
    except OSError:
        pass
    info = parse_hh(resp)
    try:
        w = int(re.search(r"\d+", info.get("PRINT WIDTH", "")).group())
        h = int(re.search(r"\d+", info.get("LABEL LENGTH", "")).group())
    except (AttributeError, ValueError):
        return None
    if w <= 50 or h <= 20:
        return None
    d = dpi_from_info(info, w)
    W, H, D = snap_size(w, h, d)
    return W, H, D, info


def size_text(W, H, D):
    return f"{W / D:.2f} x {H / D:.2f} in"


def _zfit(t, maxh, wid):
    h = wid * 0.95 / (max(len(t), 1) * 0.6)
    return int(clamp(min(h, maxh), 12, 10_000))


def zpl_label(W, H, D, serial="", usage=""):
    """Full-size ZPL test label; layout adapts to the label size."""
    clean = lambda s: re.sub(r"[\^~]", "", s or "")
    serial, usage = clean(serial), clean(usage)
    now = datetime.datetime.now()
    hin, win = H / D, W / D
    m = max(16, int(D * 0.08))
    x0, cw, ch = m, W - 2 * m, H - 2 * m
    tier = "L" if hin >= 2.6 else "M" if hin >= 1.6 else "S"
    ft, fi, fb, fr, fh = {"L": (.085, .042, .15, .25, .10),
                          "M": (.15, .08, .24, 0, .13),
                          "S": (.17, .095, .24, 0, .12)}[tier]
    info = [f"{win:.2f} x {hin:.2f} in  |  {D} dpi"]
    info.append(now.strftime("%Y-%m-%d %H:%M"))   # every label is timestamped
    if tier == "L" and serial:
        info.append("SN " + serial)
    if tier == "L" and usage:
        info.append("HEAD USAGE " + usage)
    fih = min(_zfit(t, ch * fi, cw) for t in info)

    cands = ["LABEL-TEST-" + now.strftime("%y%m%d"), "TEST-" + now.strftime("%y%m%d"),
             "12345678", "1234"]
    for i, c in enumerate(cands):
        mod = 35 + 11 * len(c)
        by = int(cw * 0.9 / mod)
        if by >= 2 or i == len(cands) - 1:
            bdata = c
            break
    by = clamp(by, 1, 5)
    bw = by * mod
    bh = int(ch * fb)
    hr = clamp(int(bh * 0.22), 14, 36)

    items = [("text", "TEST PRINT OK", _zfit("TEST PRINT OK", ch * ft, cw))]
    items += [("text", t, fih) for t in info]
    items.append(("barcode", bdata, bh + 6 + hr))
    if fr > 0:
        items.append(("row", "", int(ch * fr)))
    items.append(("hatch", "", max(16, int(ch * fh))))
    total = sum(it[2] for it in items)
    gap = max(2, (ch - total) / (len(items) - 1)) if len(items) > 1 else 0

    out = [f"^XA^PW{W}^LL{H}^LH0,0", f"^FO6,6^GB{W - 12},{H - 12},4^FS"]

    def ctext(t, y, h):
        out.append(f"^FO{x0},{int(y)}^A0N,{h},{h}^FB{cw},1,0,C^FD{t}^FS")

    y = m
    for kind, val, h in items:
        if kind == "text":
            ctext(val, y, h)
        elif kind == "barcode":
            out.append(f"^BY{by}^FO{x0 + (cw - bw) // 2},{int(y)}^BCN,{bh},N,N,N^FD{val}^FS")
            ctext(val, y + bh + 6, hr)
        elif kind == "row":
            mag = clamp(min(int((h - 12) / 29), int(cw * 0.4 / 29)), 1, 10)
            qw = 29 * mag
            g2 = int(cw * 0.05)
            sw = cw - qw - g2
            qd = f"LABEL TEST {win:.2f}X{hin:.2f}IN {D}DPI"
            qy = int(y + (h - qw) / 2 - 8)
            if sw < 160:
                out.append(f"^FO{x0 + (cw - qw) // 2},{qy}^BQN,2,{mag}^FDMA,{qd}^FS")
            else:
                out.append(f"^FO{x0},{qy}^BQN,2,{mag}^FDMA,{qd}^FS")
                sx = x0 + qw + g2
                samples = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz",
                           "0123456789 +-=/.,:;?!", "FINE PRINT 0123456789 abcdefghij"]
                lh = h / 4
                for k, s in enumerate(samples):
                    fh2 = max(12, int(D * 0.07) if k == 3 else _zfit(s, lh * 0.8, sw))
                    out.append(f"^FO{sx},{int(y + k * lh + (lh - fh2) / 2)}"
                               f"^A0N,{fh2},{fh2}^FB{sw},1,0,L^FD{s}^FS")
        elif kind == "hatch":
            # thin diagonal lines that hit every dot column without heavy black areas
            x = x0
            while x <= x0 + cw - h:
                out.append(f"^FO{x},{int(y)}^GD{h},{h},3,B,R^FS")
                x += 18
        y += h + gap
    out.append("^XZ")
    return "".join(out)


def epl_label(W, H, D):
    """Full-size test label for older EPL printers (LP/TLP 2844 etc.)."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    txt = size_text(W, H, D)
    F1 = min(H // 7, 90)
    F1 = max(min(F1, W * 115 // 1000), 20)
    F2 = max(F1 * 55 // 100, 16)
    Y1 = H * 6 // 100
    BH = clamp(H * 18 // 100, 30, 200)
    BY = 3 if W >= 600 else 2
    BX = max((W - 79 * BY) // 2, 10)
    m1 = clamp(F1 // 32, 1, 6)
    m2 = clamp(F2 // 24, 1, 6)
    ya = Y1
    yb = ya + 32 * m1 + H * 3 // 100
    yc = yb + 24 * m2 + H * 2 // 100
    ybc = yc + 24 * m2 + H * 4 // 100
    x1 = max((W - 13 * 26 * m1) // 2, 6)
    x2 = max((W - len(txt) * 18 * m2) // 2, 6)
    x3 = max((W - len(now) * 18 * m2) // 2, 6)
    HB = clamp(H * 12 // 100, 30, 160)
    HB = min(HB, H - 14 - (ybc + BH + 40))
    YH = H - HB - 14
    out = [f"\nN\nq{W}\nQ{H},24\n", f"X6,6,4,{W - 7},{H - 7}\n",
           f'A{x1},{ya},0,4,{m1},{m1},N,"TEST PRINT OK"\n',
           f'A{x2},{yb},0,3,{m2},{m2},N,"{txt}"\n',
           f'A{x3},{yc},0,3,{m2},{m2},N,"{now}"\n',
           f'B{BX},{ybc},0,1,{BY},{BY},{BH},B,"12345678"\n']
    if HB >= 16:
        x = 12
        while x <= W - 12 - HB:
            out.append(f"LS{x},{YH + HB},3,{x + HB},{YH}\n")
            x += 18
    out.append("P1\n")
    return "".join(out)


def dymo_ops(L, S, label_name, dpi=300):
    """Drawing ops for a landscape label L x S points (top-left origin)."""
    now = datetime.datetime.now()
    ops = []
    m = max(3.0, S * 0.07)
    ops.append(("stroke", 1.0, 1.0, L - 2.0, S - 2.0, 1.0))
    # printhead band: the head runs across the short side, so the band spans all of it
    bw = clamp(L * 0.16, 10, S)
    hx = L - m - bw
    ops.append(("hatch", hx, m, bw, S - 2 * m, 3.0, 0.6))
    ax, aw = m, hx - 2 * m
    avail = S - 2 * m
    items = [("text", "TEST PRINT OK", avail * 0.22, "Helvetica-Bold")]
    items.append(("text", f"{S / 72:.2f} x {L / 72:.2f} in" +
                  (f"  |  {label_name}" if label_name else ""), avail * 0.11, "Helvetica"))
    stamp = now.strftime("%Y-%m-%d %H:%M")   # every label is timestamped
    if S >= 54:
        items.append(("text", stamp, avail * 0.11, "Helvetica"))
    else:  # very narrow labels: date goes on the size line
        items[-1] = ("text", items[-1][1] + "  |  " + stamp, avail * 0.12, "Helvetica")
    dot = 72.0 / dpi
    cands = ["LABEL-TEST-" + now.strftime("%y%m%d"), "TEST-" + now.strftime("%y%m%d"),
             "12345678", "1234"]
    for i, c in enumerate(cands):
        mw = int(aw * 0.95 / code128_width(c) / dot) * dot
        if mw >= 2 * dot or i == len(cands) - 1:
            bdata, mw = c, max(mw, dot)
            break
    bh = avail * 0.28
    hr = max(4.0, avail * 0.09)
    items.append(("barcode", bdata, bh + hr + 1.5, mw))
    total = sum(it[2] for it in items)
    gap = max(1.0, (avail - total) / (len(items) - 1))
    y = m
    for it in items:
        if it[0] == "text":
            ops.append(("text", it[1], ax, y, aw, it[2], it[3]))
        else:
            data, mw = it[1], it[3]
            x = ax + (aw - code128_width(data) * mw) / 2
            for is_bar, w in code128_modules(data):
                if is_bar:
                    ops.append(("fill", x, y, w * mw, bh))
                x += w * mw
            ops.append(("text", data, ax, y + bh + 1.5, aw, hr, "Helvetica"))
        y += it[2] + gap
    return ops


def dymo_ps(w_pt, h_pt, label_name, dpi=300):
    """PostScript page exactly the label size, content turned to run along the label."""
    landscape_needed = h_pt > w_pt
    L, S = (h_pt, w_pt) if landscape_needed else (w_pt, h_pt)
    ops = dymo_ops(L, S, label_name, dpi)
    p = ["%!PS-Adobe-3.0", f"%%BoundingBox: 0 0 {int(w_pt)} {int(h_pt)}",
         f"<< /PageSize [{w_pt} {h_pt}] >> setpagedevice"]
    if landscape_needed:
        p.append(f"{w_pt} 0 translate 90 rotate")
    p.append(f"/S {S} def")
    p.append("""/fitshow { /fn exch def /sz exch def /ww exch def /yy exch def /xx exch def /s exch def
  fn findfont sz scalefont setfont
  s stringwidth pop dup ww gt { ww exch div sz mul /sz exch def fn findfont sz scalefont setfont } { pop } ifelse
  xx ww s stringwidth pop sub 2 div add  S yy sub sz 0.78 mul sub  moveto s show } def""")
    for op in ops:
        k = op[0]
        if k == "stroke":
            _, x, y, w, h, lw = op
            p.append(f"{lw} setlinewidth {x:.2f} {S - y - h:.2f} {w:.2f} {h:.2f} rectstroke")
        elif k == "fill":
            _, x, y, w, h = op
            p.append(f"{x:.3f} {S - y - h:.2f} {w:.3f} {h:.2f} rectfill")
        elif k == "text":
            _, s, x, y, w, size, font = op
            p.append(f"({ps_escape(s)}) {x:.2f} {y:.2f} {w:.2f} {size:.2f} /{font} fitshow")
        elif k == "hatch":
            _, x, y, w, h, step, lw = op
            by = S - y - h
            p.append(f"gsave {x:.2f} {by:.2f} {w:.2f} {h:.2f} rectclip {lw} setlinewidth "
                     f"{x - h:.2f} {step} {x + w:.2f} {{ dup {by:.2f} moveto {h:.2f} add "
                     f"{by + h:.2f} lineto stroke }} for grestore")
    p.append("showpage")
    return "\n".join(p) + "\n"


DYMO_ITEMS = [
    ((81, 252), "30251 / 30252 / 30320 Address"),
    ((101, 252), "30321 Large Address"),
    ((167, 288), "30256 Shipping"),
    ((153, 288), "30323 Shipping"),
    ((90, 162), "30334 Multi-Purpose"),
    ((72, 153), "30336 Small Multi-Purpose"),
    ((54, 144), "30330 Return Address"),
    ((72, 72), "30332 Square"),
    ((288, 432), "1744907 Shipping 4 x 6 (4XL only)"),
]


def dymo_item_name(w, h):
    s, l = sorted((w, h))
    for (a, b), name in DYMO_ITEMS:
        if abs(s - a) <= 4 and abs(l - b) <= 4:
            return name
    return ""


def dymo_size_label(w, h, ppd_text=""):
    """Dropdown/label text, e.g. '30251 / 30252 / 30320 Address  (1.10 x 3.50 in)'."""
    desc = f"{min(w, h) / 72:.2f} x {max(w, h) / 72:.2f} in"
    items = dymo_item_name(w, h)
    name = items or ppd_text
    if items and ppd_text and not re.search(r"\d{5}", ppd_text):
        name = f"{items} - {ppd_text}"
    return f"{name}  ({desc})" if name else desc


def dymo_driver_installed():
    r = run(["dpkg-query", "-W", "-f=${Status}", "printer-driver-dymo"])
    return "install ok installed" in r.stdout


def find_dymo_model(product):
    """Best matching driver (PPD) for this Dymo model, from lpinfo -m."""
    out = run(["lpinfo", "-m"], timeout=120).stdout
    cands = []
    for line in out.splitlines():
        if "dymo" not in line.lower():
            continue
        ppd, _, desc = line.partition(" ")
        cands.append((ppd, desc.strip()))
    p = norm(product)
    real = [c for c in cands if "sample.drv" not in c[0]]
    exact = [c for c in real if norm(c[1]) == p]
    if exact:
        return exact[0]
    partial = [c for c in real if norm(c[1]) and (norm(c[1]) in p or p in norm(c[1]))]
    if partial:
        return max(partial, key=lambda c: len(c[1]))
    sample = [c for c in cands if "sample.drv" in c[0]]
    return sample[0] if sample else None


def model_key(d):
    return norm(d["product"]) or "dymo"


def queue_name_for(product):
    """One shared printer per model, e.g. 'LabelTest-DYMO-LabelWriter-450-Turbo'."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", product.replace("DYMO", "")).strip("-") or "Printer"
    return f"LabelTest-DYMO-{base}"


def queue_sizes(queue):
    """[(name, width_pt, height_pt, label)] from the queue's driver; default first."""
    out = run(["lpoptions", "-p", queue, "-l"]).stdout
    names, default = [], None
    for line in out.splitlines():
        if line.lower().startswith(("pagesize/", "pagesize:")):
            for tok in line.split(":", 1)[1].split():
                if tok.startswith("*"):
                    tok = tok[1:]
                    default = tok
                names.append(tok)
    dims, texts = {}, {}
    ppd = read_file(f"/etc/cups/ppd/{queue}.ppd")
    for m in re.finditer(r'^\*PaperDimension\s+([^/:\s]+)(?:/[^:]*)?:\s*"([\d.]+)\s+([\d.]+)"', ppd, re.M):
        dims[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    for m in re.finditer(r"^\*PageSize\s+([^/:\s]+)/([^:]*):", ppd, re.M):
        texts[m.group(1)] = m.group(2).strip()
    sizes = []
    for n in names:
        if n in dims:
            w, h = dims[n]
        else:
            m = re.match(r"^w([\d.]+)h([\d.]+)", n)
            if not m:
                continue
            w, h = float(m.group(1)), float(m.group(2))
        sizes.append((n, w, h, texts.get(n, "")))
    sizes.sort(key=lambda s: s[0] != default)
    return sizes


def dim_id(w, h):
    """Size id independent of the driver: 'dim:<short>x<long>' in points."""
    return f"dim:{min(w, h):g}x{max(w, h):g}"


def parse_dim_id(sel):
    m = re.match(r"^dim:([\d.]+)x([\d.]+)$", sel or "") or re.match(r"^w([\d.]+)h([\d.]+)", sel or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return min(a, b), max(a, b)


def resolve_size(sizes, sel):
    """The driver's page size that matches the chosen labels (or None)."""
    for s in sizes:
        if s[0] == sel:
            return s
    dims = parse_dim_id(sel)
    if not dims:
        return None
    best = None
    for s in sizes:
        err = abs(min(s[1], s[2]) - dims[0]) + abs(max(s[1], s[2]) - dims[1])
        if err <= 6 and (best is None or err < best[0]):
            best = (err, s)
    return best[1] if best else None


def size_choices(sizes):
    """Dropdown entries [(id, label)] for a driver's sizes: known Dymo rolls first
    (near-identical driver sizes merged into one), then everything else."""
    best, other = {}, []
    for n, w, h, text in sizes:
        item = dymo_item_name(w, h)
        if not item:
            other.append((dim_id(w, h), dymo_size_label(w, h, text)))
            continue
        a, b = next(sz for sz, nm in DYMO_ITEMS if nm == item)
        err = abs(min(w, h) - a) + abs(max(w, h) - b)
        if item not in best or err < best[item][0]:
            best[item] = (err, dim_id(a, b), dymo_size_label(a, b, text))
    seen, out = set(), []
    for cid, label in [(c, l) for _e, c, l in best.values()] + other:
        if cid not in seen:
            seen.add(cid)
            out.append((cid, label))
    return out


def builtin_size_choices():
    """Common Dymo rolls - usable before the printer has ever been set up."""
    return [(dim_id(a, b), dymo_size_label(a, b)) for (a, b), _n in DYMO_ITEMS]


def canonical_id(w, h):
    """Known Dymo roll -> its nominal size id, so tiny driver differences don't matter."""
    item = dymo_item_name(w, h)
    if item:
        a, b = next(sz for sz, nm in DYMO_ITEMS if nm == item)
        return dim_id(a, b)
    return dim_id(w, h)


def label_text_for(w, h, ppd_text=""):
    item = dymo_item_name(w, h)
    if item:  # "30251 / 30252 / 30320 Address" -> "30251 Address"
        return re.findall(r"\d{5,7}", item)[0] + " " + re.sub(r"^[\d /]+", "", item)
    return ppd_text


class PrinterMixin:
    """Label-printer pages and actions; mixed into the main window."""

    def opts(self, d):
        """Per-printer options (each printer keeps its own)."""
        s = self.settings.get(d["key"])
        if s is None:
            s = {"prep": "reset", "size": "auto", "dpi": "auto",
                 "lang": "EPL" if re.search(r"2844|EPL", d["product"], re.I) else "ZPL",
                 "dsize": None, "zinfo": None}
            self.settings[d["key"]] = s
        return s

    def dymo_choice(self, d):
        """Labels chosen for this Dymo: its own choice, else the last one used for the model."""
        return self.opts(d).get("dsize") or self.cfg.get("dymo_size_model", {}).get(model_key(d))

    def print_checked(self):
        devs = [d for d in self.devices if d["key"] in self.checked]
        if not devs:
            return
        busy = [d for d in devs if d["key"] in self.busy_keys]
        devs = [d for d in devs if d["key"] not in self.busy_keys]
        if busy:
            self.log("Still working, skipped this time: " + ", ".join(short_name(d) for d in busy))
        missing = [d for d in devs if d["brand"] == "Dymo" and not self.dymo_choice(d)]
        if missing:
            self.message("Pick the labels first",
                         "Choose which labels are loaded for: " +
                         ", ".join(short_name(d) for d in missing) +
                         ".\nSelect each one on the left and pick from 'Labels loaded', "
                         "then click Print checked again.")
            for row in self.listbox.get_children():
                if row.key == missing[0]["key"]:
                    self.listbox.select_row(row)
            return
        if devs:
            self.log(f"Printing on {len(devs)} printer(s) at once…")
            self.global_task(self._batch, devs)

    def _batch(self, devs):
        dymos = [d for d in devs if d["brand"] == "Dymo"]
        # one-time model setup first (one at a time, so you get at most one password prompt each)
        for d in dymos:
            self._tl.prefix = f"[{short_name(d)}] "
            self.dymo_setup(d)
        self._tl.prefix = ""
        self.prepare_unit_queues(dymos)
        for d in devs:
            call_main(self.start_print, d)

    def start_print(self, d):
        o = self.opts(d)
        if d["brand"] == "Zebra":
            self.task(d, self.zebra_test, d, o["prep"], o["size"], o["dpi"], o["lang"])
        else:
            self.task(d, self.dymo_test, d, self.dymo_choice(d))

    def build_zebra_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(16)
        self.z_title = lbl()
        self.z_sub = lbl()
        self.z_sub.get_style_context().add_class("dim-label")
        page.pack_start(self.z_title, False, False, 0)
        page.pack_start(self.z_sub, False, False, 0)

        info = Gtk.Grid(column_spacing=18, row_spacing=4)
        self.z_info = {}
        for i, (key, title) in enumerate([("size", "Label size"), ("res", "Resolution"),
                                          ("usage", "Head usage"), ("fw", "Firmware")]):
            t = lbl(title + ":")
            t.get_style_context().add_class("dim-label")
            v = lbl("—", selectable=True)
            info.attach(t, (i % 2) * 2, i // 2, 1, 1)
            info.attach(v, (i % 2) * 2 + 1, i // 2, 1, 1)
            self.z_info[key] = v
        page.pack_start(info, False, False, 0)
        page.pack_start(Gtk.Separator(), False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.z_prep = combo([("reset", "Factory reset (recommended before selling)"),
                             ("cal", "Full calibration (feeds a few labels)"),
                             ("none", "Nothing - use current settings")])
        sizes = [("auto", "Auto-detect")] + [(f"{w}x{h}", f"{w} x {h} in") for w, h in STD_SIZES]
        self.z_size = combo(sizes)
        self.z_dpi = combo([("auto", "Auto"), ("203", "203 dpi"), ("300", "300 dpi")])
        self.z_lang = combo([("ZPL", "ZPL (most Zebras)"), ("EPL", "EPL (LP/TLP 2844 and older)")])
        for r, (t, w, opt) in enumerate([("Before printing:", self.z_prep, "prep"),
                                         ("Label size:", self.z_size, "size"),
                                         ("Resolution:", self.z_dpi, "dpi"),
                                         ("Printer language:", self.z_lang, "lang")]):
            grid.attach(lbl(t), 0, r, 1, 1)
            grid.attach(w, 1, r, 1, 1)
            w.connect("changed", self.on_zebra_opt, opt)
            self.page_widgets.append(w)
        page.pack_start(grid, False, False, 0)
        note = lbl("These settings are kept separately for each Zebra.", wrap=True)
        note.get_style_context().add_class("dim-label")
        page.pack_start(note, False, False, 0)

        big = Gtk.Button(label="Print test label")
        big.get_style_context().add_class("suggested-action")
        big.set_size_request(-1, 44)
        big.connect("clicked", lambda *_: self.current() and self.start_print(self.current()))
        page.pack_start(big, False, False, 0)
        self.page_widgets.append(big)

        row = Gtk.Box(spacing=8)
        for text, tip, fn in [
            ("Printer info", "Read serial, head usage, firmware and settings", self.zebra_info),
            ("Config label", "Print the printer's own configuration label", self.zebra_config),
            ("Factory reset", "Reset to factory settings only (re-measures labels)", self.zebra_reset),
            ("Calibrate", "Full media calibration only", self.zebra_calibrate),
        ]:
            b = Gtk.Button(label=text)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fn: self.task(self.current(), f, self.current()))
            row.pack_start(b, True, True, 0)
            self.page_widgets.append(b)
        page.pack_start(row, False, False, 0)
        return page

    def on_zebra_opt(self, widget, opt):
        d = self.current()
        if self._loading or not d or d["brand"] != "Zebra":
            return
        self.opts(d)[opt] = widget.get_active_id()

    def load_zebra_opts(self, d):
        o = self.opts(d)
        self._loading = True
        self.z_prep.set_active_id(o["prep"])
        self.z_size.set_active_id(o["size"])
        self.z_dpi.set_active_id(o["dpi"])
        self.z_lang.set_active_id(o["lang"])
        self._loading = False
        self._show_zinfo(d)

    def _show_zinfo(self, d):
        zi = self.opts(d).get("zinfo")
        for v in self.z_info.values():
            v.set_text("—")
        if not zi:
            return
        W, H, D, info = zi
        if W:
            self.z_info["size"].set_text(f"{size_text(W, H, D)}  ({W} x {H} dots)")
            self.z_info["res"].set_text(f"{D} dpi")
        if info:
            self.z_info["usage"].set_text(info.get("TOTAL USAGE", info.get("HEAD USAGE", "—")))
            self.z_info["fw"].set_text(info.get("FIRMWARE", "—").replace("<-", "").strip())

    def z_info_show(self, d, W=None, H=None, D=None, info=None):
        self.opts(d)["zinfo"] = (W, H, D, info)

        def _do():
            if d["key"] == self.selected_key:
                self._show_zinfo(d)
            return False
        GLib.idle_add(_do)

    def zebra_reconnect(self, d, reset=False):
        """Replug the printer in software and wait for /dev/usb/lp* to come back: first by
        re-attaching its driver, then if needed by resetting its USB connection.
        Returns the new device path or None."""
        for step_reset, wait in ((False, 4), (True, 10)):
            if reset and not step_reset:
                continue
            cur = self.find_again(d["key"], "Zebra") or d
            ok, how = usb_replug(cur["sysdir"], "07", step_reset)
            if not ok:
                self.log(f"Couldn't reconnect the printer automatically: {how}.")
                if how.startswith("no access"):
                    self.log("Run ./install.sh again - it adds the permission for this.")
                return None
            end = time.time() + wait
            while time.time() < end:
                time.sleep(1)
                again = self.find_again(d["key"], "Zebra")
                if again and again["lp"] and os.path.exists(again["lp"]):
                    time.sleep(1)                     # let permissions settle on the new node
                    d["lp"], d["sysdir"] = again["lp"], again["sysdir"]
                    self.log(f"Reconnected the printer ({how}).")
                    return again["lp"]
        self.log("The printer didn't come back after reconnecting.")
        return None

    def zebra_dev(self, d):
        if not d:
            self.log("No printer selected.")
            return None
        if not d["lp"]:
            self.log("The printer's USB connection dropped - reconnecting it…")
            if not self.zebra_reconnect(d):
                self.log("This Zebra isn't available as /dev/usb/lp*. Unplug it and plug it back in. "
                         "If that doesn't help, run:  sudo modprobe usblp")
                return None
        if not os.access(d["lp"], os.W_OK):
            self.log(f"No permission to use {d['lp']}. Run this once in a terminal, then log "
                     "out and back in:  sudo usermod -aG lp $USER")
            return None
        return d["lp"]

    def zebra_wait_detect(self, d, secs):
        """Keep asking until the printer answers (it's silent while busy). After a factory
        reset or calibration the printer restarts its USB connection, and Linux sometimes
        doesn't reconnect it - so if it stays gone or silent, replug it in software."""
        start = time.time()
        end = start + secs
        dev = d["lp"]
        missing_since = None
        replugged = False
        while time.time() < end:
            if not dev or not os.path.exists(dev):
                again = self.find_again(d["key"], "Zebra")
                dev = again["lp"] if again else None
                if not dev:
                    missing_since = missing_since or time.time()
                    if not replugged and again and time.time() - missing_since > 6:
                        replugged = True
                        self.log("The printer didn't reconnect after restarting - reconnecting it…")
                        dev = self.zebra_reconnect(d)
                    if not dev:
                        time.sleep(2)
                    continue
            missing_since = None
            r = zebra_detect(dev)
            if r:
                return dev, r
            if not replugged and time.time() - start > 30:
                replugged = True
                self.log("The printer isn't answering - reconnecting it…")
                dev = self.zebra_reconnect(d, reset=True) or dev
                continue
            time.sleep(2)
        return dev, None

    def ask_zebra_size(self, d):
        def _dlg():
            dlg = Gtk.Dialog(title=f"Label size - {short_name(d)}", transient_for=self, modal=True)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Print", Gtk.ResponseType.OK)
            box = dlg.get_content_area()
            box.set_spacing(8)
            box.set_border_width(12)
            box.add(lbl(f"<b>{GLib.markup_escape_text(display_name(d))}</b>  (SN {d['serial']})\n"
                        "Couldn't read the label size from this printer. Pick it here:", markup=True))
            s = combo([(f"{w}x{h}", f"{w} x {h} in") for w, h in STD_SIZES])
            r = combo([("203", "203 dpi (most desktop Zebras)"), ("300", "300 dpi")])
            box.add(s)
            box.add(r)
            dlg.show_all()
            ok = dlg.run() == Gtk.ResponseType.OK
            res = (s.get_active_id(), int(r.get_active_id())) if ok else None
            dlg.destroy()
            return res
        return call_main(_dlg)

    def zebra_test(self, d, prep, size_id, dpi_id, lang):
        dev = self.zebra_dev(d)
        if not dev:
            return False
        if lang == "EPL":
            if size_id == "auto":
                self.log("EPL printers can't report their label size - pick it in the window that opened.")
                picked = self.ask_zebra_size(d)
                if not picked:
                    self.log("Cancelled.")
                    return False
                size_id, D = picked
            else:
                D = int(dpi_id) if dpi_id != "auto" else 203
            w, h = (float(v) for v in size_id.split("x"))
            W, H = int(w * D), int(h * D)
            zebra_write(dev, epl_label(W, H, D))
            self.log(f"Sent EPL test label ({size_text(W, H, D)}, {D} dpi).")
            self.cert_mark(d, "Test label sent", "pass", f"EPL label, {size_text(W, H, D)}, {D} dpi")
            return True

        secs = 12
        if prep == "reset":
            self.log("Restoring factory settings… the printer will feed a couple of labels to measure them.")
            self._factory_reset(d, dev)
            secs = 75
        elif prep == "cal":
            self.log("Calibrating… the printer will feed a few labels.")
            zebra_write(dev, "~JC")
            secs = 60
        time.sleep(3)
        self.log("Waiting for the printer to finish and report its label size…")
        dev, det = self.zebra_wait_detect(d, secs)
        info = det[3] if det else {}
        if det:
            W, H, D, info = det
            self.log(f"Detected {size_text(W, H, D)} at {D} dpi.")
            self.z_info_show(d, W, H, D, info)
            self.cert_mark(d, "Label size detected", "pass", f"{size_text(W, H, D)} at {D} dpi")
            if prep == "reset":
                self.cert_mark(d, "Factory reset", "pass", "Factory defaults restored and saved (^JUF, ^JUS)")
            elif prep == "cal":
                self.cert_mark(d, "Media calibration", "pass", "Labels re-measured (~JC)")
        if size_id != "auto" or not det:
            if size_id == "auto":
                self.log("Couldn't read the label size from the printer.")
                self.cert_mark(d, "Label size detected", "fail", "The printer didn't report its label size")
                picked = self.ask_zebra_size(d)
                if not picked:
                    self.log("Cancelled.")
                    return False
                size_id, D = picked
            else:
                D = int(dpi_id) if dpi_id != "auto" else (det[2] if det else 203)
            w, h = (float(v) for v in size_id.split("x"))
            W, H = int(w * D), int(h * D)
            self.log(f"Using the size you picked: {size_text(W, H, D)} at {D} dpi.")
        elif dpi_id != "auto" and int(dpi_id) != D:
            D2 = int(dpi_id)
            W, H = int(W / D * D2), int(H / D * D2)
            D = D2
        if not dev:
            self.log("The printer disappeared. Check the cable and try again.")
            return False
        serial = info.get("SERIAL NUMBER", d["serial"])
        usage = info.get("TOTAL USAGE", "")
        zebra_write(dev, zpl_label(W, H, D, serial, usage))
        self.log("Sent full-size test label.")
        self.cert_mark(d, "Test label sent", "pass", f"Full-size label {size_text(W, H, D)} with printhead check band")
        return True

    def _factory_reset(self, d, dev):
        zebra_write(dev, "^XA^JUF^XZ")       # load factory defaults (printer re-measures labels)
        time.sleep(3)
        if not os.path.exists(dev):
            again = self.find_again(d["key"], "Zebra")
            dev = again["lp"] if again else dev
        try:
            zebra_write(dev, "^XA^JUS^XZ")   # save, so it sticks after a power cycle
        except OSError:
            self.log("Couldn't save the factory settings - the printer may have restarted.")

    def zebra_info(self, d):
        dev = self.zebra_dev(d)
        if not dev:
            return False
        self.log("Reading printer info…")
        resp = zebra_query(dev, "^XA^HH^XZ", 5)
        if not resp.strip():
            self.log("The printer didn't answer. If it's busy, wait a moment and try again. "
                     "Older EPL-only printers can't report their settings.")
            return False
        info = parse_hh(resp)
        det = zebra_detect(dev)
        if det:
            self.z_info_show(d, det[0], det[1], det[2], info)
        else:
            prev = self.opts(d).get("zinfo") or (None, None, None, None)
            self.z_info_show(d, prev[0], prev[1], prev[2], info)
        self.cert_mark(d, "Printer info read", "pass", "Serial, firmware and usage read from the printer")
        keys = ["SERIAL NUMBER", "FIRMWARE", "RESOLUTION", "TOTAL USAGE", "HEAD USAGE",
                "LAST CLEANED", "DARKNESS", "PRINT SPEED", "PRINT WIDTH", "LABEL LENGTH",
                "MEDIA TYPE", "SENSOR TYPE", "PRINT MODE", "ZPL MODE"]
        lines = [f"  {k.title():<15} {info[k].replace('<-', '').strip()}" for k in keys if k in info]
        self.log("Printer info:\n" + "\n".join(lines))
        return True

    def zebra_config(self, d):
        dev = self.zebra_dev(d)
        if not dev:
            return False
        zebra_write(dev, "U\n" if self.opts(d)["lang"] == "EPL" else "~WC")
        self.log("Sent: print configuration label.")
        self.cert_mark(d, "Configuration label printed", "info", "Printer's own configuration label")
        return True

    def zebra_reset(self, d):
        dev = self.zebra_dev(d)
        if not dev:
            return False
        self.log("Restoring factory settings… the printer will feed a couple of labels.")
        self._factory_reset(d, dev)
        time.sleep(3)
        dev, det = self.zebra_wait_detect(d, 75)
        if det:
            self.z_info_show(d, *det)
            self.log(f"Factory reset done. Labels measured as {size_text(*det[:3])} at {det[2]} dpi.")
            self.cert_mark(d, "Factory reset", "pass", "Factory defaults restored and saved (^JUF, ^JUS)")
            return True
        self.log("Factory reset sent, but the printer didn't report back yet.")
        self.cert_mark(d, "Factory reset", "info", "Sent; the printer didn't report back")
        return False

    def zebra_calibrate(self, d):
        dev = self.zebra_dev(d)
        if not dev:
            return False
        self.log("Calibrating… the printer will feed a few labels.")
        zebra_write(dev, "~JC")
        time.sleep(3)
        dev, det = self.zebra_wait_detect(d, 60)
        if det:
            self.z_info_show(d, *det)
            self.log(f"Calibration done. Labels measured as {size_text(*det[:3])}.")
            self.cert_mark(d, "Media calibration", "pass", f"Labels measured as {size_text(*det[:3])}")
            return True
        self.log("Calibration sent, but the printer didn't report back yet.")
        return False

    def build_dymo_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(16)
        self.d_title = lbl()
        self.d_sub = lbl()
        self.d_sub.get_style_context().add_class("dim-label")
        page.pack_start(self.d_title, False, False, 0)
        page.pack_start(self.d_sub, False, False, 0)
        self.d_status = lbl("", wrap=True)
        page.pack_start(self.d_status, False, False, 0)
        page.pack_start(Gtk.Separator(), False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.attach(lbl("Labels loaded:"), 0, 0, 1, 1)
        self.d_size = Gtk.ComboBoxText()
        self.d_size.set_size_request(360, -1)
        self.d_size.connect("changed", self.on_dymo_size)
        grid.attach(self.d_size, 1, 0, 1, 1)
        self.page_widgets.append(self.d_size)
        hint = lbl("Dymo printers can't sense the labels - pick the roll that's loaded.\n"
                   "Each printer keeps its own choice; a new printer starts with the last "
                   "roll used on that model.", wrap=True)
        hint.get_style_context().add_class("dim-label")
        grid.attach(hint, 1, 1, 1, 1)
        page.pack_start(grid, False, False, 0)

        big = Gtk.Button(label="Print test label")
        big.get_style_context().add_class("suggested-action")
        big.set_size_request(-1, 44)
        big.connect("clicked", lambda *_: self.current() and self.start_print(self.current()))
        page.pack_start(big, False, False, 0)
        self.page_widgets.append(big)

        row = Gtk.Box(spacing=8)
        for text, tip, fn in [
            ("Set up again", "Reinstall this model with the correct Dymo driver", self.dymo_resetup),
            ("Clear print queue", "Cancel stuck jobs and re-enable the printer", self.dymo_clear),
        ]:
            b = Gtk.Button(label=text)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fn: self.task(self.current(), f, self.current()))
            row.pack_start(b, True, True, 0)
            self.page_widgets.append(b)
        page.pack_start(row, False, False, 0)
        return page

    def on_dymo_size(self, widget):
        d = self.current()
        if self._loading or not d or d["brand"] != "Dymo":
            return
        sel = widget.get_active_id()
        if not sel:
            return
        self.opts(d)["dsize"] = sel
        self.cfg.setdefault("dymo_size_model", {})[model_key(d)] = sel
        save_config(self.cfg)

    def d_refresh_status(self, d):
        """Show driver/setup status and fill the label list (in the background)."""
        self.fill_sizes(d, None)   # usable right away, refined below

        def _work():
            driver = dymo_driver_installed()
            queue = self.dymo_queue_for(d)
            sizes = queue_sizes(queue) if queue else []
            GLib.idle_add(self._d_show_status, d, driver, queue, sizes)
        threading.Thread(target=_work, daemon=True).start()

    def _d_show_status(self, d, driver, queue, sizes):
        if d["key"] != self.selected_key:
            return False
        if re.search(r"\b5[0-9]{2}\b|5XL", d["product"]):
            text = ("⚠ This looks like a LabelWriter 550-series. Those only print on genuine "
                    "Dymo labels and the Linux driver doesn't support them well - printing "
                    "may not work from Ubuntu.")
        elif queue:
            text = (f"✔ Ready. Every {d['product'].replace('DYMO', '').strip()} you plug in "
                    "uses the same setup, so there's nothing to set up for this one.")
        elif not driver:
            text = ("The Dymo driver isn't installed yet. Printing will install it "
                    "(you'll be asked for your password) and set this model up - only once.")
        else:
            text = ("First time seeing this model - printing will set it up once, then every "
                    "printer of this model is ready right away.")
        self.d_status.set_text(text)
        if sizes:
            self.fill_sizes(d, sizes)
        return False

    def fill_sizes(self, d, sizes):
        """Fill 'Labels loaded'. Works before setup too (common Dymo rolls)."""
        choices = size_choices(sizes) if sizes else builtin_size_choices()
        sel = self.dymo_choice(d)
        dims = parse_dim_id(sel)
        if dims:
            sel = canonical_id(*dims)
            if sel not in [c[0] for c in choices]:
                choices.append((sel, dymo_size_label(*dims)))
        self._loading = True
        self.d_size.remove_all()
        if not sel:
            self.d_size.append("", "(pick the labels that are loaded)")
        for cid, label in choices:
            self.d_size.append(cid, label)
        self.d_size.set_active_id(sel or "")
        self._loading = False

    def ask_dymo_size(self, d, sizes):
        def _dlg():
            dlg = Gtk.Dialog(title=f"Labels loaded - {short_name(d)}", transient_for=self, modal=True)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Print", Gtk.ResponseType.OK)
            box = dlg.get_content_area()
            box.set_spacing(8)
            box.set_border_width(12)
            box.add(lbl(f"<b>{GLib.markup_escape_text(display_name(d))}</b>  (SN {d['serial']})\n"
                        "Which labels are loaded in this printer?", markup=True))
            c = combo(size_choices(sizes) or builtin_size_choices())
            box.add(c)
            dlg.show_all()
            res = c.get_active_id() if dlg.run() == Gtk.ResponseType.OK else None
            dlg.destroy()
            return res
        return call_main(_dlg)

    def dymo_queue_for(self, d):
        """The shared printer set up for this Dymo model (any unit of it), if any."""
        queues = cups_queues()
        name = self.cfg.get("dymo_model_queues", {}).get(model_key(d))
        if name in queues:
            return name
        name = queue_name_for(d["product"])  # set up earlier but config was lost
        if name in queues and "serial=" not in queues[name]:
            self.cfg.setdefault("dymo_model_queues", {})[model_key(d)] = name
            save_config(self.cfg)
            return name
        return None

    def dymo_uri(self, d):
        """USB address for this model without the serial number."""
        uri = None
        for _q, quri in cups_queues().items():
            if quri.startswith("usb://") and d["serial"] and uri_serial(quri) == d["serial"]:
                uri = quri
                break
        if not uri:
            r = run(["lpinfo", "--include-schemes", "usb", "-v"], timeout=60)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith("usb://") and "dymo" in parts[1].lower() \
                        and (not d["serial"] or uri_serial(parts[1]) == d["serial"]):
                    uri = parts[1]
                    break
        if not uri:
            uri = "usb://DYMO/" + urllib.parse.quote(d["product"].replace("DYMO", "").strip())
        return uri.split("?")[0]

    def dymo_setup(self, d, force=False):
        """Driver installed + this model set up. Once per model, one at a time."""
        with self.admin_lock:
            queue = self.dymo_queue_for(d)
            if queue and not force:
                return queue
            if not dymo_driver_installed():
                self.log("Installing the Dymo driver (printer-driver-dymo). Enter your password "
                         "when asked - this can take a minute…")
                r = run(["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install",
                         "-y", "printer-driver-dymo"], timeout=900)
                if r.returncode != 0 or not dymo_driver_installed():
                    err = (r.stderr or r.stdout).strip().splitlines()
                    self.log("Driver install didn't finish" + (": " + err[-1] if err else ".") +
                             "\nYou can install it yourself with:  sudo apt install printer-driver-dymo")
                else:
                    self.log("Dymo driver installed.")
            model = find_dymo_model(d["product"])
            if not model:
                self.log("No Dymo driver found. Install it with:  sudo apt install printer-driver-dymo")
                return None
            if "sample.drv" in model[0]:
                self.log("Using CUPS's basic Dymo driver (the full Dymo driver wasn't available).")
            uri = self.dymo_uri(d)
            name = queue or queue_name_for(d["product"])
            model_name = d["product"].replace("DYMO", "").strip()
            self.log(f"Setting up '{name}' with driver: {model[1]}")
            ok = admin_run([
                ["lpadmin", "-p", name, "-v", uri, "-m", model[0],
                 "-D", f"{display_name(d)} (any unit - Label Printer Tester)", "-L", "USB"],
                ["cupsenable", name],
                ["cupsaccept", name],
            ], self.log)
            if not ok:
                return None
            self.cfg.setdefault("dymo_model_queues", {})[model_key(d)] = name
            self.cfg.setdefault("dymo_model_ppd", {})[model_key(d)] = model[0]
            save_config(self.cfg)
            self.log(f"Set up. Every {model_name} you plug in from now on is ready to print right away.")
            sizes = queue_sizes(name)
            GLib.idle_add(self._d_show_status, d, True, name, sizes)
            return name

    def unit_queue_name(self, d):
        return f"{queue_name_for(d['product'])}-{d['serial'][-6:]}"

    def same_model_connected(self, d):
        return [x for x in scan_usb() if x["brand"] == "Dymo" and model_key(x) == model_key(d)]

    def prepare_unit_queues(self, dymos):
        """With 2+ identical Dymos plugged in, the shared setup can't tell them apart -
        give each unit its own entry (all at once, so at most one password prompt)."""
        with self.admin_lock:
            queues = cups_queues()
            cmds, made = [], []
            for d in dymos:
                if len(self.same_model_connected(d)) < 2 or not d["serial"]:
                    continue
                name = self.unit_queue_name(d)
                if name in queues or name in made:
                    continue
                ppd = self.cfg.get("dymo_model_ppd", {}).get(model_key(d))
                if not ppd:
                    m = find_dymo_model(d["product"])
                    ppd = m[0] if m else None
                if not ppd:
                    continue
                uri = f"{self.dymo_uri(d)}?serial={d['serial']}"
                cmds += [["lpadmin", "-p", name, "-v", uri, "-m", ppd, "-D",
                          f"{display_name(d)} SN {d['serial']} (Label Printer Tester)", "-L", "USB"],
                         ["cupsenable", name], ["cupsaccept", name]]
                made.append(name)
            if cmds:
                self.log("More than one identical Dymo is plugged in - giving each its own "
                         "printer entry so labels go to the right one.")
                admin_run(cmds, self.log)

    def dymo_target_queue(self, d):
        """Where this unit's job should go."""
        model_q = self.dymo_setup(d)
        if not model_q:
            return None
        if len(self.same_model_connected(d)) >= 2 and d["serial"]:
            self.prepare_unit_queues([d])
            name = self.unit_queue_name(d)
            if name in cups_queues():
                return name
            self.log("Couldn't give this printer its own entry - with identical Dymos plugged "
                     "in, the label may come out of the other one.")
        return model_q

    def dymo_test(self, d, sel):
        if not d:
            return False
        queue = self.dymo_target_queue(d)
        if not queue:
            return False
        sizes = queue_sizes(queue)
        if not sizes:
            self.log("The driver didn't list any label sizes; printing with its default size.")
            w, h, sname, text = 162.0, 90.0, None, ""
        else:
            s = resolve_size(sizes, sel)
            if not s:
                self.log("Which labels are loaded? Pick them in the window that opened.")
                sel = self.ask_dymo_size(d, sizes)
                s = resolve_size(sizes, sel) if sel else None
                if not s:
                    self.log("Cancelled.")
                    return False
            sname, w, h, text = s
            chosen = canonical_id(w, h)
            self.opts(d)["dsize"] = chosen
            self.cfg.setdefault("dymo_size_model", {})[model_key(d)] = chosen
            save_config(self.cfg)
            GLib.idle_add(lambda: (self.selected_key == d["key"] and self.fill_sizes(d, sizes), False)[1])
        text = label_text_for(w, h, text)
        ps = dymo_ps(w, h, text)
        with tempfile.NamedTemporaryFile("w", suffix=".ps", delete=False) as f:
            f.write(ps)
            path = f.name
        cmd = ["lp", "-d", queue] + (["-o", f"PageSize={sname}"] if sname else []) + [path]
        r = run(cmd)
        os.unlink(path)
        if r.returncode != 0:
            self.log("Printing failed: " + (r.stderr.strip() or r.stdout.strip()))
            self.cert_mark(d, "Test label sent", "fail", r.stderr.strip() or r.stdout.strip())
            return False
        m = re.search(r"request id is (\S+)", r.stdout)
        job = m.group(1) if m else None
        desc = f"{min(w, h) / 72:.2f} x {max(w, h) / 72:.2f} in"
        self.log(f"Sent test label ({text + ', ' if text else ''}{desc}).")
        self.cert_mark(d, "Test label sent", "pass", f"{text + ', ' if text else ''}{desc}")
        # check on the job in the background - the page is usable again right away
        self.in_thread(self.watch_job, d, queue, job, prefix=self._tl.prefix)
        return True

    def watch_job(self, d, queue, job, secs=30):
        """Tell you if a job gets stuck, without blocking anything."""
        if not job:
            return
        end = time.time() + secs
        while time.time() < end:
            time.sleep(2)
            if job not in run(["lpstat", "-o", queue]).stdout:
                self.cert_mark(d, "Print job completed", "pass", "The label left the print queue")
                return   # went through
        state = run(["lpstat", "-l", "-p", queue]).stdout.strip()
        self.set_row_status(d["key"], "⚠ Job stuck - see Activity")
        self.cert_mark(d, "Print job completed", "fail", "The label stayed in the print queue")
        self.log("The label is still waiting in the print queue. Printer status:\n" + state +
                 "\nTry 'Clear print queue', unplug/replug the printer, then print again.")

    def dymo_resetup(self, d):
        if not d:
            return False
        return bool(self.dymo_setup(d, force=True))

    def dymo_clear(self, d):
        if not d:
            return False
        queues = [q for q in (self.dymo_queue_for(d), self.unit_queue_name(d) if d["serial"] else None)
                  if q and q in cups_queues()]
        if not queues:
            self.log("This printer isn't set up yet - nothing to clear.")
            return False
        cmds = []
        for q in queues:
            run(["cancel", "-a", q])
            cmds += [["cupsenable", q], ["cupsaccept", q]]
        admin_run(cmds, self.log)
        self.log("Cleared the print queue and re-enabled the printer.")
        return True

    def forget_unplugged_units(self):
        """Quietly remove per-unit Dymo entries for printers that were unplugged
        (only if that's allowed without a password - otherwise Clean up handles them)."""
        serials, _p = all_usb_ids()
        for name, uri in cups_queues().items():
            s = uri_serial(uri)
            if name.startswith("LabelTest-") and s and s not in serials:
                run(["cancel", "-a", name])
                run(["lpadmin", "-x", name])

    def cleanup_dialog(self):
        queues = cups_queues()
        if not queues:
            self.message("No printers are set up on this computer.")
            return
        serials, products = all_usb_ids()
        rows = []
        for name, uri in sorted(queues.items()):
            if name.startswith("LabelTest-") and "serial=" not in uri:
                rows.append([False, name, "Shared setup for this model - keep "
                             "(used by every unit you plug in)", uri])
            elif uri.startswith("usb://"):
                s = uri_serial(uri)
                model = norm(urllib.parse.unquote(urllib.parse.urlparse(uri).path.strip("/")))
                connected = (s in serials) if s else any(model and model in p for p in products)
                status = "USB - connected" if connected else "USB - not connected"
                rows.append([not connected, name, status, uri])
            else:
                kind = uri.split(":", 1)[0]
                rows.append([False, name, f"Network ({kind}) - leave unless you know it's gone", uri])

        dlg = Gtk.Dialog(title="Clean up printers", transient_for=self, modal=True)
        dlg.set_default_size(720, 420)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Remove checked", Gtk.ResponseType.OK)
        area = dlg.get_content_area()
        area.set_spacing(8)
        area.set_border_width(12)
        area.add(lbl("USB printers that aren't plugged in are checked for removal. "
                     "Network printers (like office printers) are never checked automatically.",
                     wrap=True))
        store = Gtk.ListStore(bool, str, str, str)
        for r in rows:
            store.append(r)
        tv = Gtk.TreeView(model=store)
        tog = Gtk.CellRendererToggle()
        tog.connect("toggled", lambda _r, path: store.set_value(
            store.get_iter(path), 0, not store[path][0]))
        tv.append_column(Gtk.TreeViewColumn("Remove", tog, active=0))
        for i, title in [(1, "Printer"), (2, "Status")]:
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, cell, text=i)
            col.set_resizable(True)
            col.set_expand(True)
            tv.append_column(col)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.add(tv)
        area.pack_start(sw, True, True, 0)
        dlg.show_all()
        resp = dlg.run()
        chosen = [row[1] for row in store if row[0]]
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not chosen:
            return
        self.global_task(self.remove_queues, chosen)

    def remove_queues(self, names):
        self.log("Removing: " + ", ".join(names))
        for n in names:
            run(["cancel", "-a", n])
        with self.admin_lock:
            ok = admin_run([["lpadmin", "-x", n] for n in names], self.log)
        if ok:
            left = cups_queues()
            gone = [n for n in names if n not in left]
            self.log(f"Removed {len(gone)} printer(s).")
            dq = self.cfg.get("dymo_model_queues", {})
            for k in [k for k, v in dq.items() if v in gone]:
                del dq[k]
            save_config(self.cfg)
        d = self.current()
        if d and d["brand"] == "Dymo":
            GLib.idle_add(lambda: (self.d_refresh_status(d), False)[1])
