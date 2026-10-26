"""Barcode scanners: see every scan live (per scanner), on-screen and printed test
codes with automatic checks, and a per-model library of factory-reset codes."""

import fcntl
import select
import struct
import termios

from .common import *  # noqa: F401,F403
from .gtkutil import GdkPixbuf, GLib, Gtk, call_main, combo, dim, lbl

# ------------------------------------------------------------------ test codes
# (id, name, kind, data, zint symbology number)
TEST_CODES = [
    ("code128", "Code 128", "1D", "BT-128-OK", 20),
    ("code39", "Code 39", "1D", "BT39OK", 8),
    ("upca", "UPC-A", "1D", "01234567890", 34),      # check digit 5 added -> 012345678905
    ("ean13", "EAN-13", "1D", "590123412345", 13),    # check digit 7 added -> 5901234123457
    ("qr", "QR Code", "2D", "BENCH TESTED QR OK", 58),
    ("datamatrix", "Data Matrix", "2D", "BENCH TESTED DM OK", 71),
    ("pdf417", "PDF417", "2D", "BT PDF417 OK", 55),
]
CODE_BY_ID = {c[0]: c for c in TEST_CODES}


def match_scan(text):
    """Which test code (id) a scanned string came from, if any."""
    t = text.strip().upper()
    digits = re.sub(r"\D", "", t)
    for cid, _n, _k, data, _z in TEST_CODES:
        if cid in ("upca", "ean13"):
            # scanners may drop/add leading zeros or the check digit
            core = data[1:]
            if core in digits and len(digits) <= len(data) + 2:
                return cid
        elif data.upper() in t:
            return cid
    return None


def zint_path():
    return shutil.which("zint")


def make_code_image(cid, out_dir, scale=3):
    """SVG file for a test code: zint for every symbology, built-in Code 128 fallback."""
    _id, _n, _k, data, zsym = CODE_BY_ID[cid]
    path = os.path.join(out_dir, f"{cid}.svg")
    if zint_path():
        r = run(["zint", "-b", str(zsym), "--scale", str(scale), "--height", "40",
                 "-o", path, "-d", data], timeout=20)
        if r.returncode == 0 and os.path.exists(path):
            return path
    if cid == "code128":
        with open(path, "w") as f:
            f.write(code128_svg(data))
        return path
    return None


def code128_svg(data, module=3, height=110, quiet=10):
    mods = code128_modules(data)
    width = (sum(w for _b, w in mods) + 2 * quiet) * module
    rects, x = [], quiet * module
    for is_bar, w in mods:
        if is_bar:
            rects.append(f'<rect x="{x}" y="0" width="{w * module}" height="{height}"/>')
        x += w * module
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height + 26}">'
            f'<rect width="100%" height="100%" fill="white"/>{"".join(rects)}'
            f'<text x="{width / 2}" y="{height + 20}" font-family="monospace" font-size="18" '
            f'text-anchor="middle">{data}</text></svg>')


# ------------------------------------------------------------------ printed test sheets

def zpl_test_labels(W, H, D):
    """Test codes as Zebra labels - as many per label as fit, any label size."""
    block = int(160 * D / 203)
    per = max(1, H // block)
    block = H // per
    labels = []
    for i in range(0, len(TEST_CODES), per):
        z = [f"^XA^PW{W}^LL{H}^LH0,0"]
        for j, (cid, name, kind, data, _zs) in enumerate(TEST_CODES[i:i + per]):
            y0 = j * block + int(block * 0.06)
            th = clamp(int(block * 0.14), 14, int(26 * D / 203))
            z.append(f"^FO0,{y0}^A0N,{th},{th}^FB{W},1,0,C^FD{name}  ({kind})^FS")
            ch = block - th - int(block * 0.18)
            cy = y0 + th + int(block * 0.04)
            if cid == "code128":
                by = clamp(int(W * 0.8 / code128_width(data)), 1, 4)
                x = (W - by * code128_width(data)) // 2
                z.append(f"^BY{by}^FO{x},{cy}^BCN,{max(20, ch - 24)},Y,N,N^FD{data}^FS")
            elif cid == "code39":
                by = clamp(int(W * 0.8 / ((len(data) + 2) * 16)), 1, 3)
                x = (W - by * (len(data) + 2) * 16) // 2
                z.append(f"^BY{by}^FO{max(10, x)},{cy}^B3N,N,{max(20, ch - 24)},Y,N^FD{data}^FS")
            elif cid == "upca":
                by = clamp(int(W * 0.8 / 113), 1, 4)
                z.append(f"^BY{by}^FO{(W - by * 113) // 2},{cy}^BUN,{max(20, ch - 30)},Y,N,Y^FD{data}^FS")
            elif cid == "ean13":
                by = clamp(int(W * 0.8 / 113), 1, 4)
                z.append(f"^BY{by}^FO{(W - by * 113) // 2},{cy}^BEN,{max(20, ch - 30)},Y,N^FD{data}^FS")
            elif cid == "qr":
                mag = clamp(ch // 29, 1, 10)
                z.append(f"^FO{(W - 29 * mag) // 2},{cy - 10}^BQN,2,{mag}^FDMA,{data}^FS")
            elif cid == "datamatrix":
                m = clamp(ch // 22, 2, 12)
                z.append(f"^FO{(W - 20 * m) // 2},{cy}^BXN,{m},200^FD{data}^FS")
            elif cid == "pdf417":
                rh = clamp(ch // 12, 2, 10)
                by = clamp(int(W / 300), 1, 3)
                z.append(f"^BY{by}^FO{max(10, (W - by * 220) // 2)},{cy}^B7N,{rh},3,4,,N^FD{data}^FS")
        z.append("^XZ")
        labels.append("".join(z))
    return labels


def eps_bbox(eps):
    m = re.search(r"%%BoundingBox:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", eps)
    return tuple(int(v) for v in m.groups()) if m else None


def ps_test_sheet():
    """Letter-size PostScript test sheet for any office printer. Uses zint's EPS output
    for every symbology (built-in Code 128 if zint isn't installed)."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    p = ["%!PS-Adobe-3.0", "%%BoundingBox: 0 0 612 792", "<< /PageSize [612 792] >> setpagedevice",
         "/Helvetica-Bold findfont 20 scalefont setfont 54 740 moveto (Bench Tested - scanner test sheet) show",
         f"/Helvetica findfont 10 scalefont setfont 54 724 moveto ({now}  -  scan each code; "
         "laser scanners read the 1D codes only) show"]
    cell_w, cell_h = 252, 150
    tmp = tempfile.mkdtemp(prefix="bt-sheet-")
    for i, (cid, name, kind, data, zsym) in enumerate(TEST_CODES):
        col, row = i % 2, i // 2
        x0 = 54 + col * (cell_w + 30)
        y0 = 700 - (row + 1) * cell_h
        p.append(f"/Helvetica-Bold findfont 12 scalefont setfont {x0} {y0 + cell_h - 18} moveto "
                 f"({ps_escape(name)}  \\({kind}\\)) show")
        eps = None
        if zint_path():
            path = os.path.join(tmp, cid + ".eps")
            r = run(["zint", "-b", str(zsym), "--filetype=EPS", "--scale", "1", "--height", "40",
                     "-o", path, "-d", data], timeout=20)
            if r.returncode == 0 and os.path.exists(path):
                eps = open(path, errors="replace").read()
        bb = eps_bbox(eps) if eps else None
        if bb:
            bw, bh = bb[2] - bb[0], bb[3] - bb[1]
            s = min((cell_w - 20) / max(bw, 1), (cell_h - 40) / max(bh, 1), 3.0)
            p.append(f"save /showpage {{}} def {x0} {y0 + 10} translate {s:.3f} {s:.3f} scale "
                     f"{-bb[0]} {-bb[1]} translate\n%%BeginDocument: {cid}.eps\n{eps}\n%%EndDocument\nrestore")
        elif cid == "code128":
            mods = code128_modules(data)
            mw = min(1.5, (cell_w - 20) / sum(w for _b, w in mods))
            x = x0
            for is_bar, w in mods:
                if is_bar:
                    p.append(f"{x:.2f} {y0 + 30} {w * mw:.2f} 70 rectfill")
                x += w * mw
            p.append(f"/Courier findfont 11 scalefont setfont {x0} {y0 + 14} moveto ({data}) show")
        else:
            p.append(f"/Helvetica findfont 10 scalefont setfont {x0} {y0 + 60} moveto "
                     "(Install 'zint' to print this code) show")
    p.append("showpage")
    shutil.rmtree(tmp, ignore_errors=True)
    return "\n".join(p) + "\n"


# ------------------------------------------------------------------ reading scanners
# Linux input key codes -> (normal, shifted) characters, US layout
_KEYS = {
    2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(", 11: "0)",
    12: "-_", 13: "=+", 16: "qQ", 17: "wW", 18: "eE", 19: "rR", 20: "tT", 21: "yY", 22: "uU",
    23: "iI", 24: "oO", 25: "pP", 26: "[{", 27: "]}", 30: "aA", 31: "sS", 32: "dD", 33: "fF",
    34: "gG", 35: "hH", 36: "jJ", 37: "kK", 38: "lL", 39: ";:", 40: "'\"", 41: "`~", 43: "\\|",
    44: "zZ", 45: "xX", 46: "cC", 47: "vV", 48: "bB", 49: "nN", 50: "mM", 51: ",<", 52: ".>",
    53: "/?", 57: "  ", 55: "**", 71: "77", 72: "88", 73: "99", 74: "--", 75: "44", 76: "55",
    77: "66", 78: "++", 79: "11", 80: "22", 81: "33", 82: "00", 83: "..", 98: "//",
}
KEY_ENTER, KEY_KPENTER, KEY_TAB = 28, 96, 15
KEY_LSHIFT, KEY_RSHIFT, KEY_CAPS = 42, 54, 58
EV_KEY = 1
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EVIOCGRAB = 0x40044590


class KeyDecoder:
    """Turns key events from a keyboard-mode scanner into scanned strings."""

    def __init__(self):
        self.buf, self.shift, self.caps = [], False, False

    def feed(self, code, value):
        """Returns a finished scan (on Enter/Tab) or None."""
        if code in (KEY_LSHIFT, KEY_RSHIFT):
            self.shift = value != 0
            return None
        if value == 0:
            return None
        if code == KEY_CAPS:
            self.caps = not self.caps
            return None
        if code in (KEY_ENTER, KEY_KPENTER, KEY_TAB):
            return self.flush()
        ch = _KEYS.get(code)
        if ch:
            up = self.shift
            if ch[0].isalpha() and self.caps:
                up = not up
            self.buf.append(ch[1] if up else ch[0])
        return None

    def flush(self):
        text, self.buf = "".join(self.buf), []
        return text or None


def has_key(bitmap, code):
    """Does an input device's key-capability bitmap (sysfs hex words) include `code`?"""
    words = bitmap.split()
    idx = len(words) - 1 - code // 64
    if idx < 0:
        return False
    try:
        return bool(int(words[idx], 16) >> (code % 64) & 1)
    except ValueError:
        return False


def keyboard_nodes(sysdir):
    """/dev/input/event* nodes of this USB device that can type letters/digits."""
    nodes = []
    for ev in glob.glob("/sys/class/input/event*"):
        real = os.path.realpath(ev + "/device")
        if not real.startswith(sysdir + "/"):
            continue
        caps = read_file(ev + "/device/capabilities/key")
        if has_key(caps, 30) or has_key(caps, 2):    # KEY_A or KEY_1
            nodes.append("/dev/input/" + os.path.basename(ev))
    return sorted(nodes)


def serial_nodes(sysdir):
    nodes = []
    for tty in glob.glob("/sys/class/tty/ttyACM*") + glob.glob("/sys/class/tty/ttyUSB*"):
        real = os.path.realpath(tty + "/device")
        if real.startswith(sysdir + "/"):
            nodes.append("/dev/" + os.path.basename(tty))
    return sorted(nodes)


def scanner_mode(d):
    """'keyboard', 'serial', 'hidpos' or 'unknown' - how the scanner talks to the computer."""
    if keyboard_nodes(d["sysdir"]):
        return "keyboard"
    if serial_nodes(d["sysdir"]):
        return "serial"
    if any(c == "03" for c, _s, _p in d["ifaces"]):
        return "hidpos"
    return "unknown"


MODE_TEXT = {
    "keyboard": "USB keyboard mode (types what it scans)",
    "serial": "USB serial / COM port mode",
    "hidpos": "HID POS / OPOS mode - it won't type scans. Scan the 'USB Keyboard (HID)' "
              "code from its manual, or its factory reset code.",
    "unknown": "Unknown connection mode",
}


class ScannerListener:
    """Reads one scanner in the background and calls on_scan(key, text)."""

    def __init__(self, d, on_scan, on_state):
        self.d, self.on_scan, self.on_state = d, on_scan, on_state
        self.stop_flag = False
        self.state = "starting"
        self.mode = scanner_mode(d)
        self.started = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag = True

    def _set(self, state):
        self.state = state
        self.on_state(self.d["key"], state)

    def _run(self):
        mode = self.mode
        try:
            if mode == "keyboard":
                self._run_keyboard()
            elif mode == "serial":
                self._run_serial()
            else:
                self._set(mode)
        except Exception:
            self._set("error")

    def _run_keyboard(self):
        fds = {}
        for node in keyboard_nodes(self.d["sysdir"]):
            try:
                fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                self._set("noperm")
                return
            except OSError:
                continue
            try:   # keep scans from also typing into whatever window is focused
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError:
                pass
            fds[fd] = KeyDecoder()
        if not fds:
            self._set("unknown")
            return
        self._set("listening")
        last = time.time()
        try:
            while not self.stop_flag:
                r, _w, _x = select.select(list(fds), [], [], 0.15)
                if not r:
                    if time.time() - last > 0.25:      # no Enter suffix: end of scan by pause
                        for dec in fds.values():
                            text = dec.flush()
                            if text:
                                self.on_scan(self.d["key"], text)
                    continue
                for fd in r:
                    try:
                        data = os.read(fd, EVENT_SIZE * 64)
                    except OSError:
                        self._set("gone")
                        return
                    last = time.time()
                    for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                        _s, _us, etype, code, value = struct.unpack(EVENT_FMT, data[i:i + EVENT_SIZE])
                        if etype == EV_KEY:
                            text = fds[fd].feed(code, value)
                            if text:
                                self.on_scan(self.d["key"], text)
        finally:
            for fd in fds:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 0)
                except OSError:
                    pass
                os.close(fd)

    def _run_serial(self):
        node = serial_nodes(self.d["sysdir"])[0]
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            self._set("noperm-serial")
            return
        try:
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~(termios.ICANON | termios.ECHO)    # raw-ish
            attrs[0] &= ~(termios.ICRNL | termios.IXON)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error:
            pass
        self._set("listening")
        buf, last = b"", time.time()
        try:
            while not self.stop_flag:
                r, _w, _x = select.select([fd], [], [], 0.15)
                if r:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        self._set("gone")
                        return
                    buf += chunk
                    last = time.time()
                    while re.search(rb"[\r\n]", buf):
                        line, buf = re.split(rb"[\r\n]+", buf, maxsplit=1)
                        if line.strip():
                            self.on_scan(self.d["key"], line.decode("latin-1").strip())
                elif buf and time.time() - last > 0.25:
                    self.on_scan(self.d["key"], buf.decode("latin-1").strip())
                    buf = b""
        finally:
            os.close(fd)


# ------------------------------------------------------------------ reset-code library

RESET_DIR = os.path.join(DATA_DIR, "reset-codes")
RESET_INDEX = os.path.join(RESET_DIR, "index.json")


def model_id(d):
    return f"{d['vid']}:{d['pid']}"


def reset_library():
    try:
        with open(RESET_INDEX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def reset_entry(d):
    """Saved reset code for this scanner model: matched by USB id, then by product name."""
    lib = reset_library()
    if model_id(d) in lib:
        return lib[model_id(d)]
    for e in lib.values():
        if e.get("product") and norm(e["product"]) == norm(d["product"]):
            return e
    return None


def save_reset_entry(d, src_path, name):
    os.makedirs(RESET_DIR, exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower() or ".png"
    dest = os.path.join(RESET_DIR, re.sub(r"[^A-Za-z0-9]+", "-", model_id(d)) + ext)
    shutil.copyfile(src_path, dest)
    lib = reset_library()
    lib[model_id(d)] = {"file": dest, "name": name, "product": d["product"],
                        "added": datetime.datetime.now().strftime("%Y-%m-%d")}
    tmp = RESET_INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lib, f, indent=2)
    os.replace(tmp, RESET_INDEX)
    return lib[model_id(d)]


# ================================================================== page

class BarcodeMixin:
    """Barcode-scanner page; mixed into the main window."""

    def barcode_init(self):
        self.listeners = {}          # key -> ScannerListener
        self.scan_state = {}         # key -> {"hits": set, "log": [(time, text, cid)], "state": str}
        self.code_dir = tempfile.mkdtemp(prefix="bench-tested-codes-")
        self.code_cards = {}         # cid -> (status label)

    def scan_info(self, key):
        return self.scan_state.setdefault(key, {"hits": set(), "log": [], "state": "starting"})

    # ---------------------------------------------------------- listeners follow the device list
    def barcode_devices_changed(self, devs):
        scanners = {d["key"]: d for d in devs if d["category"] == "scanner"}
        for key in list(self.listeners):
            if key not in scanners or self.listeners[key].d.get("plug") != scanners[key].get("plug"):
                self.listeners.pop(key).stop()
                if key not in scanners or not scanners[key]["serial"]:
                    self.scan_state.pop(key, None)       # a different unit: fresh results
                    self.set_row_status(key, "")
        for key, d in scanners.items():
            if key not in self.listeners or self.listeners[key].stop_flag:
                self.listeners[key] = ScannerListener(d, self._on_scan_thread, self._on_state_thread)

    def barcode_tick(self):
        """Runs every second: a just-plugged-in scanner may not have its keyboard/serial
        connection yet - reconnect once it (or a different one) appears."""
        for d in self.devices:
            if d["category"] != "scanner":
                continue
            lst = self.listeners.get(d["key"])
            mode = scanner_mode(d)
            failed = lst is not None and lst.state in ("noperm", "noperm-serial", "error", "gone") \
                and time.time() - getattr(lst, "started", 0) > 1.0      # e.g. opened before access was granted
            if lst is None or lst.stop_flag or failed or getattr(lst, "mode", None) != mode:
                if lst:
                    lst.stop()
                self.listeners[d["key"]] = ScannerListener(d, self._on_scan_thread, self._on_state_thread)

    def barcode_restart(self, d):
        old = self.listeners.pop(d["key"], None)
        if old:
            old.stop()
        GLib.timeout_add(400, lambda: (self.barcode_devices_changed(self.devices), False)[1])

    def _on_state_thread(self, key, state):
        GLib.idle_add(self._on_state, key, state)

    def _on_state(self, key, state):
        self.scan_info(key)["state"] = state
        if key == self.selected_key and self.current() and self.current()["category"] == "scanner":
            self.show_listen_state(self.current())
        if state == "hidpos":
            self.set_row_status(key, "⚠ Not in keyboard mode")
        elif state == "listening" and self.row_status.get(key, "").startswith("⚠ Not in keyboard"):
            self.set_row_status(key, "")         # it was only still connecting
        return False

    def _on_scan_thread(self, key, text):
        GLib.idle_add(self.handle_scan, key, text)

    def handle_scan(self, key, text):
        info = self.scan_info(key)
        cid = match_scan(text)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        info["log"].append((stamp, text, cid))
        new = cid and cid not in info["hits"]
        if cid:
            info["hits"].add(cid)
        d = self.device(key)
        name = short_name(d) if d else "scanner"
        what = f"{CODE_BY_ID[cid][1]} ✓" if cid else "not one of the test codes"
        self.log(f"[{name}] scanned: {text}  ->  {what}")
        n1 = len([c for c in info["hits"] if CODE_BY_ID[c][2] == "1D"])
        n2 = len([c for c in info["hits"] if CODE_BY_ID[c][2] == "2D"])
        self.set_row_status(key, f"✓ {len(info['log'])} scan{'s' if len(info['log']) != 1 else ''}"
                                 f"  ·  1D {n1}/4  2D {n2}/3")
        if key == self.selected_key:
            self.append_scan_line(stamp, text, cid, new)
            self.update_code_marks(info)
        return False

    # ---------------------------------------------------------- page
    def build_barcode_page(self):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(16)
        outer.add(page)
        self.b_title = lbl()
        self.b_sub = dim(lbl(selectable=True))
        self.b_mode = lbl(wrap=True)
        self.b_listen = lbl(wrap=True)
        for w in (self.b_title, self.b_sub, self.b_mode, self.b_listen):
            page.pack_start(w, False, False, 0)

        # live scan output
        frame = Gtk.Frame(label=" Scans from this scanner ")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        self.b_out = Gtk.TextView()
        self.b_out.set_editable(False)
        self.b_out.set_cursor_visible(False)
        self.b_out.set_monospace(True)
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(-1, 110)
        sw.add(self.b_out)
        box.pack_start(sw, True, True, 0)
        self.b_entry = Gtk.Entry()
        self.b_entry.set_placeholder_text("If scans don't show up above: click here, then scan")
        self.b_entry.connect("activate", self.on_entry_scan)
        box.pack_start(self.b_entry, False, False, 0)
        frame.add(box)
        page.pack_start(frame, False, False, 0)

        self.b_summary = lbl(markup=True)
        page.pack_start(self.b_summary, False, False, 0)

        # test codes
        hdr = Gtk.Box(spacing=8)
        hdr.pack_start(lbl("<b>Test codes</b> - scan each one. Laser scanners can't read "
                           "screens: print a sheet instead.", markup=True, wrap=True), True, True, 0)
        page.pack_start(hdr, False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_column_spacing(10)
        flow.set_row_spacing(10)
        self.code_cards = {}
        for cid, name, kind, data, _z in TEST_CODES:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.set_border_width(6)
            img = Gtk.Image()
            img.set_size_request(230, 120)
            card.pack_start(img, False, False, 0)
            mark = lbl(f"○  {name} ({kind})", xalign=0.5)
            card.pack_start(mark, False, False, 0)
            flow.add(card)
            self.code_cards[cid] = (img, mark)
        page.pack_start(flow, False, False, 0)
        self.b_zint_hint = dim(lbl("Only Code 128 can be shown without the 'zint' barcode tool. "
                                   "Click 'Install barcode tool' to show the rest.", wrap=True))
        page.pack_start(self.b_zint_hint, False, False, 0)

        row = Gtk.Box(spacing=8)
        for text, tip, fn in [
            ("Print test sheet…", "Print every test code on a label printer or office printer", self.print_sheet_dialog),
            ("Factory reset code…", "Show the reset barcode for this scanner model", self.reset_dialog),
            ("Clear results", "Start this scanner's test over", self.clear_scanner),
        ]:
            b = Gtk.Button(label=text)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fn: f())
            row.pack_start(b, True, True, 0)
        self.b_install = Gtk.Button(label="Install barcode tool")
        self.b_install.connect("clicked", lambda *_: self.global_task(self.install_zint))
        row.pack_start(self.b_install, True, True, 0)
        page.pack_start(row, False, False, 0)
        self.render_codes()
        return outer

    def render_codes(self):
        have_zint = bool(zint_path())
        for cid, (img, _mark) in self.code_cards.items():
            path = make_code_image(cid, self.code_dir)
            if path:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 230, 120, True)
                    img.set_from_pixbuf(pb)
                    continue
                except Exception:
                    pass
            img.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
        self.b_zint_hint.set_visible(not have_zint)
        self.b_install.set_visible(not have_zint)

    def install_zint(self):
        self.log("Installing the 'zint' barcode tool - enter your password when asked…")
        r = run(["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "zint"],
                timeout=600)
        if zint_path():
            self.log("Barcode tool installed.")
            GLib.idle_add(lambda: (self.render_codes(), False)[1])
        else:
            self.log("Couldn't install it: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1] +
                     "\nYou can install it yourself with:  sudo apt install zint")

    def barcode_select(self, d):
        self.b_title.set_markup(f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>")
        self.b_sub.set_text(f"Serial {d['serial'] or '?'}  ·  USB id {d['vid']}:{d['pid']}")
        self.b_mode.set_text("Connection: " + MODE_TEXT.get(scanner_mode(d), "?"))
        self.show_listen_state(d)
        info = self.scan_info(d["key"])
        buf = self.b_out.get_buffer()
        buf.set_text("")
        for stamp, text, cid in info["log"]:
            self.append_scan_line(stamp, text, cid, False)
        self.update_code_marks(info)
        self.b_entry.grab_focus()

    def show_listen_state(self, d):
        state = self.scan_info(d["key"])["state"]
        text = {
            "listening": "✔ Listening - scans from this scanner show up below, even while "
                         "you look at another device.",
            "noperm": "Scan into the box below. (To tell several scanners apart automatically, run "
                      "'sudo usermod -aG input $USER' once and log out and back in.)",
            "noperm-serial": "No permission for the scanner's serial port. Run "
                             "'sudo usermod -aG dialout $USER' once, then log out and back in.",
            "hidpos": "⚠ This scanner isn't sending scans as typing. See 'Factory reset code…'.",
            "gone": "The scanner was unplugged.",
            "error": "Couldn't read this scanner - scan into the box below instead.",
        }.get(state, "Connecting…")
        self.b_listen.set_text(text)

    def append_scan_line(self, stamp, text, cid, new):
        buf = self.b_out.get_buffer()
        what = f"   ✓ {CODE_BY_ID[cid][1]}" if cid else ""
        buf.insert(buf.get_end_iter(), f"{stamp}  {text}{what}\n")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.b_out.scroll_mark_onscreen(mark)

    def update_code_marks(self, info):
        for cid, (_img, mark) in self.code_cards.items():
            _i, name, kind, _d, _z = CODE_BY_ID[cid]
            mark.set_text(f"{'✓' if cid in info['hits'] else '○'}  {name} ({kind})")
        n1 = len([c for c in info["hits"] if CODE_BY_ID[c][2] == "1D"])
        n2 = len([c for c in info["hits"] if CODE_BY_ID[c][2] == "2D"])
        if not info["log"]:
            s = "No scans yet."
        else:
            s = f"<b>1D codes read: {n1}/4    2D codes read: {n2}/3</b>"
            if n1 and not n2:
                s += "   (no 2D yet - linear/laser scanners can't read 2D codes)"
            if n1 == 4 and n2 == 3:
                s = "<b>✓ All test codes read</b> - this scanner reads 1D and 2D codes."
        self.b_summary.set_markup(s)

    def on_entry_scan(self, entry):
        text = entry.get_text()
        entry.set_text("")
        d = self.current()
        if text.strip() and d and d["category"] == "scanner":
            self.handle_scan(d["key"], text)

    def clear_scanner(self):
        d = self.current()
        if not d:
            return
        self.scan_state[d["key"]] = {"hits": set(), "log": [], "state": self.scan_info(d["key"])["state"]}
        self.set_row_status(d["key"], "")
        self.barcode_select(d)

    # ---------------------------------------------------------- printing test sheets
    def print_sheet_dialog(self):
        zebras = [d for d in self.devices if d["category"] == "printer" and d["brand"] == "Zebra" and d["lp"]]
        office = [q for q in cups_queues() if not q.startswith("LabelTest-")]
        default = (re.search(r"destination:\s*(\S+)", run(["lpstat", "-d"]).stdout) or [None, None])[1]
        office.sort(key=lambda q: q != default)
        items = [(f"zebra:{d['key']}", f"Zebra label printer - {short_name(d)}") for d in zebras]
        items += [(f"cups:{q}", f"Office printer - {q}" + (" (default)" if q == default else "")) for q in office]
        if not items:
            self.message("No printer to print on", "Plug in a Zebra, or set up an office printer in "
                         "System printer settings.")
            return
        dlg = Gtk.Dialog(title="Print scanner test codes", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Print", Gtk.ResponseType.OK)
        area = dlg.get_content_area()
        area.set_spacing(8)
        area.set_border_width(12)
        area.add(lbl("Print every test code so laser scanners can be tested too.\n"
                     "On a Zebra, the codes are spread over as many labels as needed.", wrap=True))
        c = combo(items)
        area.add(c)
        dlg.show_all()
        ok = dlg.run() == Gtk.ResponseType.OK
        choice = c.get_active_id()
        dlg.destroy()
        if not ok:
            return
        kind, _, target = choice.partition(":")
        if kind == "zebra":
            d = self.device(target)
            if d:
                self.task(d, self.print_sheet_zebra, d)
        else:
            self.global_task(self.print_sheet_cups, target)

    def print_sheet_zebra(self, d):
        det = zebra_detect(d["lp"]) if d["lp"] else None
        if det:
            W, H, D = det[0], det[1], det[2]
        else:
            W, H, D = 812, 1218, 203
            self.log("Couldn't read the label size - assuming 4 x 6 in.")
        labels = zpl_test_labels(W, H, D)
        zebra_write(d["lp"], "".join(labels))
        self.log(f"Printed the scanner test codes on {len(labels)} label(s).")
        return True

    def print_sheet_cups(self, queue):
        if not zint_path():
            self.log("Note: without the 'zint' tool only the Code 128 test code is printed.")
        ps = ps_test_sheet()
        with tempfile.NamedTemporaryFile("w", suffix=".ps", delete=False) as f:
            f.write(ps)
            path = f.name
        r = run(["lp", "-d", queue, path])
        os.unlink(path)
        if r.returncode == 0:
            self.log(f"Sent the scanner test sheet to {queue}.")
        else:
            self.log("Printing failed: " + (r.stderr.strip() or r.stdout.strip()))

    # ---------------------------------------------------------- factory reset codes
    def reset_dialog(self):
        d = self.current()
        if not d:
            return
        entry = reset_entry(d)
        dlg = Gtk.Dialog(title=f"Factory reset - {short_name(d)}", transient_for=self, modal=True)
        dlg.set_default_size(560, -1)
        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_border_width(12)
        area.add(lbl(f"<b>{GLib.markup_escape_text(display_name(d))}</b>  (USB id {model_id(d)})",
                     markup=True))
        if entry and os.path.exists(entry["file"]):
            area.add(lbl("Scan this code to reset the scanner to factory settings.\n"
                         "Custom settings are erased, and the scanner goes back to its default "
                         "connection mode (usually USB keyboard).", wrap=True))
            if entry["file"].lower().endswith(".pdf"):
                b = Gtk.Button(label="Open the reset page (PDF)")
                b.connect("clicked", lambda *_: subprocess.Popen(["xdg-open", entry["file"]]))
                area.add(b)
            else:
                img = Gtk.Image()
                try:
                    img.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(entry["file"], 520, 420, True))
                except Exception:
                    img.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
                area.add(img)
            area.add(dim(lbl(f"Saved as '{entry.get('name', '')}' on {entry.get('added', '?')}.")))
            dlg.add_buttons("Replace…", 1, "Close", Gtk.ResponseType.CLOSE)
        else:
            area.add(lbl("No reset code saved for this model yet.\n\n"
                         "Find the 'Set Factory Defaults' / 'Default' barcode in this scanner's "
                         "programming guide, take a screenshot of it (or save the page as a PDF), "
                         "and add it here once. From then on it shows up automatically for every "
                         "scanner of this model.", wrap=True))
            dlg.add_buttons("Find the manual online", 2, "Add reset code…", 1, "Close", Gtk.ResponseType.CLOSE)
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp == 1:
            self.add_reset_code(d)
        elif resp == 2:
            q = urllib.parse.quote_plus(f"{d['manufacturer'] or d['brand']} {d['product']} "
                                        "programming guide factory default barcode")
            subprocess.Popen(["xdg-open", f"https://duckduckgo.com/?q={q}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def add_reset_code(self, d):
        fc = Gtk.FileChooserDialog(title="Choose the reset barcode (screenshot or PDF)",
                                   transient_for=self, action=Gtk.FileChooserAction.OPEN)
        fc.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use this", Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("Images and PDFs")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.svg", "*.pdf", "*.PNG", "*.JPG", "*.PDF"):
            flt.add_pattern(pat)
        fc.add_filter(flt)
        pics = os.path.expanduser("~/Pictures/Screenshots")
        if os.path.isdir(pics):
            fc.set_current_folder(pics)
        ok = fc.run() == Gtk.ResponseType.OK
        path = fc.get_filename()
        fc.destroy()
        if not ok or not path:
            return
        e = save_reset_entry(d, path, os.path.basename(path))
        self.log(f"Saved the reset code for {d['product']} ({model_id(d)}). It will show up for "
                 "every scanner of this model.")
        if e:
            self.reset_dialog()


# zebra helpers used by the test-sheet printer
from .printers import zebra_detect, zebra_write  # noqa: E402
