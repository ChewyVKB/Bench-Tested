"""Signature pads (Topaz serial "BSB" models): draw what's written on the pad live,
check the whole writing area works, save the signature as proof."""

import select
import termios

from .common import *  # noqa: F401,F403
from .gtkutil import GLib, Gtk, dim, lbl

try:
    import cairo  # noqa: F401  (needed for drawing; python3-gi-cairo)
    HAVE_CAIRO = True
except Exception:  # pragma: no cover
    HAVE_CAIRO = False

# Topaz BSB pads: 19200 baud, 7 data bits, odd parity. 5-byte packets:
#   [status (bit 7 set, bit 0 = pen down)] [x low 7] [x high 7] [y low 7] [y high 7]
TOPAZ_DEFAULT_BOUNDS = (500, 350, 2650, 1050)    # x0, y0, x1, y1 - grows if the pad reports more
PROOF_DIR = os.path.expanduser("~/Pictures/Bench Tested")


def parse_topaz(buf, seven_bit=False):
    """Pull complete packets out of buf. Returns (points, leftover) with points as
    (pen_down, x, y). Packets are 5 bytes: a status byte (0xE5 = pen touching,
    0xE4 = pen hovering near the pad), then x low/high and y low/high, 7 bits each. If the port hands over 7-bit bytes, the status byte is
    recognised by value (0x64/0x65) and confirmed by the next packet."""
    pts, i, n = [], 0, len(buf)
    while i < n:
        b0 = buf[i]
        if seven_bit:
            if b0 not in (0x64, 0x65):
                i += 1
                continue
            if i + 6 > n:
                break                                   # wait to confirm with the next packet
            if buf[i + 5] not in (0x64, 0x65):
                i += 1
                continue
        else:
            if not b0 & 0x80:
                i += 1
                continue
            if i + 5 > n:
                break
            if any(b & 0x80 for b in buf[i + 1:i + 5]):
                i += 1
                continue
        st, xl, xh, yl, yh = buf[i:i + 5]
        # status bit 0: 1 = pen touching (0xE5), 0 = pen hovering near the pad (0xE4)
        pts.append((bool(st & 1), (xl & 0x7f) | (xh & 0x7f) << 7, (yl & 0x7f) | (yh & 0x7f) << 7))
        i += 5
    return pts, buf[i:][-64:]


def port_users(node):
    """Other processes (that we can see) holding this serial port open."""
    users = []
    me = os.getpid()
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = int(fd_dir.split("/")[2])
        if pid == me:
            continue
        try:
            for f in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, f)) == node:
                    name = read_file(f"/proc/{pid}/comm") or str(pid)
                    users.append(f"{name} (pid {pid})")
                    break
        except OSError:
            continue
    return users


def tty_nodes(sysdir):
    nodes = []
    for tty in glob.glob("/sys/class/tty/ttyUSB*") + glob.glob("/sys/class/tty/ttyACM*"):
        if os.path.realpath(tty + "/device").startswith(sysdir + "/"):
            nodes.append("/dev/" + os.path.basename(tty))
    return sorted(nodes)


class PadReader:
    """Reads a Topaz serial pad in the background.
    on_points(list), on_state(str), on_raw(total_bytes, last_bytes)."""

    # clocal: ignore the modem "carrier" line - the pad's USB chip leaves it off, and without
    # this the port acts as if it were hung up and every read comes back empty.
    STTY = ["19200", "cs7", "parenb", "parodd", "-cstopb", "raw", "-echo", "clocal", "-hupcl"]

    def __init__(self, d, on_points, on_state, on_raw=None):
        self.d, self.on_points, self.on_state = d, on_points, on_state
        self.on_raw = on_raw or (lambda n, last: None)
        self.stop_flag = False
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag = True

    def _configure(self, node, fd):
        """19200 7O1 raw, ignoring modem-control lines (CLOCAL)."""
        try:
            a = termios.tcgetattr(fd)
            a[0] = 0
            a[1] = 0
            a[2] = (a[2] & ~(termios.CSIZE | termios.CSTOPB | termios.HUPCL | termios.CRTSCTS)) | \
                termios.CS7 | termios.PARENB | termios.PARODD | termios.CREAD | termios.CLOCAL
            a[3] = 0
            a[4] = a[5] = termios.B19200
            a[6][termios.VMIN] = 1
            a[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, a)
        except termios.error:
            pass

    def _open(self, node):
        """Configure with stty first (exactly the settings proven to give clean data, and
        never in 'wait for carrier' mode), then open. termios is only a fallback."""
        ok = run(["stty", "-F", node] + self.STTY, timeout=5).returncode == 0
        fd = os.open(node, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        if not ok:
            self._configure(node, fd)
        return fd

    def _run(self):
        nodes = tty_nodes(self.d["sysdir"])
        if not nodes:
            self.on_state("noport")
            return
        node = nodes[0]
        try:
            fd = self._open(node)
        except PermissionError:
            self.on_state("noperm")
            return
        except OSError:
            self.on_state("noport")
            return
        others = port_users(node)
        self.on_state("busy:" + ", ".join(others) if others else "listening")
        buf, total, last_report, tail, empties, saw_high = b"", 0, 0.0, b"", 0, False
        try:
            while not self.stop_flag:
                r, _w, _x = select.select([fd], [], [], 0.05)
                if not r:
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                except OSError:
                    self.on_state("gone")
                    return
                if not chunk:
                    # the port says "hung up": reopen it with the carrier line ignored
                    empties += 1
                    os.close(fd)
                    fd = -1
                    if empties > 20 or not os.path.exists(node):
                        self.on_state("gone")
                        return
                    time.sleep(0.3)
                    try:
                        fd = self._open(node)
                    except OSError:
                        self.on_state("gone")
                        return
                    continue
                empties = 0
                total += len(chunk)
                tail = (tail + chunk)[-15:]
                if time.time() - last_report > 0.3:
                    last_report = time.time()
                    self.on_raw(total, tail)
                buf += chunk
                if any(b & 0x80 for b in chunk):
                    saw_high = True
                pts, buf = parse_topaz(buf, seven_bit=not saw_high and total > 40)
                if pts:
                    self.on_points(pts)
        finally:
            if fd >= 0:
                os.close(fd)


class SigPadMixin:
    """Signature-pad page; mixed into the main window."""

    def sigpad_init(self):
        self.pad_state = {}

    def pad_info(self, d):
        s = self.pad_state.get(d["key"])
        if s is None:
            b = self.cfg.get("sigpad_bounds", {}).get(norm(d["product"]) or "topaz")
            s = {"strokes": [], "reader": None, "state": "starting", "points": 0,
                 "bounds": list(b or TOPAZ_DEFAULT_BOUNDS), "seen": None, "pen_was_down": False, "hover": None,
                 "last_t": 0.0}
            self.pad_state[d["key"]] = s
        return s

    def sigpad_devices_changed(self, devs):
        pads = {d["key"]: d for d in devs if d["category"] == "sigpad"}
        for key, s in self.pad_state.items():
            if key not in pads and s["reader"]:
                s["reader"].stop()
                s["reader"] = None
        for key, d in pads.items():
            s = self.pad_info(d)
            if s["reader"] is None and d["vid"] == "0403":
                s["reader"] = PadReader(d, lambda p, k=key: GLib.idle_add(self.pad_points, k, p),
                                        lambda st, k=key: GLib.idle_add(self.pad_state_changed, k, st),
                                        lambda n, last, k=key: GLib.idle_add(self.pad_raw, k, n, last))

    def pad_raw(self, key, total, last):
        d = self.device(key)
        if d:
            s = self.pad_info(d)
            s["raw"] = (total, last)
            if key == self.selected_key and not s["points"]:
                self.pad_refresh(d)
        return False

    def pad_state_changed(self, key, st):
        d = self.device(key)
        if d:
            self.pad_info(d)["state"] = st
            if key == self.selected_key:
                self.pad_refresh(d)
        return False

    def pad_points(self, key, pts):
        d = self.device(key)
        if not d:
            return False
        s = self.pad_info(d)
        now = time.time()
        b = s["bounds"]
        if now - s["last_t"] > 0.15:      # pen was lifted long enough to start a new stroke
            s["pen_was_down"] = False
        for down, x, y in pts:
            if not down:                       # hovering: show where the pen is, don't draw
                s["pen_was_down"] = False
                s["hover"] = (x, y)
                continue
            s["hover"] = None
            if x < b[0] or y < b[1] or x > b[2] or y > b[3]:    # pad reports a bigger area
                b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)
            if not s["pen_was_down"] or not s["strokes"]:
                s["strokes"].append([])
            s["strokes"][-1].append((x, y))
            s["pen_was_down"] = True
            s["points"] += 1
            sx = s["seen"]
            s["seen"] = [x, y, x, y] if sx is None else [min(sx[0], x), min(sx[1], y),
                                                         max(sx[2], x), max(sx[3], y)]
        s["last_t"] = now
        if key == self.selected_key and any(not p[0] for p in pts):
            self.p_canvas.queue_draw()
        if s["points"] and not s.get("logged"):
            s["logged"] = True
            self.log(f"[{short_name(d)}] pen writing detected ✓")
        self.set_row_status(key, self.pad_summary(d))
        if key == self.selected_key:
            self.p_canvas.queue_draw()
            self.pad_refresh(d)
        return False

    def pad_coverage(self, s):
        """Which edges of the writing area the pen has reached."""
        if not s["seen"]:
            return {}
        b, v = s["bounds"], s["seen"]
        w, h = max(1, b[2] - b[0]), max(1, b[3] - b[1])
        return {"left": v[0] - b[0] < w * 0.15, "right": b[2] - v[2] < w * 0.15,
                "top": v[1] - b[1] < h * 0.2, "bottom": b[3] - v[3] < h * 0.2}

    def pad_summary(self, d):
        s = self.pad_info(d)
        if not s["points"]:
            return ""
        cov = self.pad_coverage(s)
        n = sum(cov.values())
        return f"✓ pen works  ·  edges reached {n}/4"

    # ---------------------------------------------------------- page
    def build_sigpad_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(16)
        self.p_title = lbl()
        self.p_sub = dim(lbl(selectable=True))
        self.p_status = lbl(wrap=True)
        for w in (self.p_title, self.p_sub, self.p_status):
            page.pack_start(w, False, False, 0)
        page.pack_start(lbl("Sign on the pad - it shows up below as you write. Then run the pen into "
                            "all four edges to check the whole writing area works.", wrap=True), False, False, 0)
        frame = Gtk.Frame()
        self.p_canvas = Gtk.DrawingArea()
        self.p_canvas.set_size_request(-1, 230)
        self.p_canvas.connect("draw", self.pad_draw)
        frame.add(self.p_canvas)
        page.pack_start(frame, True, True, 0)
        self.p_checks = lbl(markup=True)
        page.pack_start(self.p_checks, False, False, 0)
        row = Gtk.Box(spacing=8)
        b = Gtk.Button(label="Clear")
        b.connect("clicked", lambda *_: self.pad_clear())
        row.pack_start(b, True, True, 0)
        self.p_flip = Gtk.CheckButton(label="Upside down")
        self.p_flip.set_tooltip_text("Tick if the drawing comes out upside down for this model")
        self.p_flip.connect("toggled", self.pad_flip_toggled)
        row.pack_start(self.p_flip, False, False, 0)
        b = Gtk.Button(label="Save signature")
        b.set_tooltip_text("Save what's drawn as a timestamped image (proof of test)")
        b.connect("clicked", lambda *_: self.pad_save())
        row.pack_start(b, True, True, 0)
        page.pack_start(row, False, False, 0)
        self.p_coords = dim(lbl())
        page.pack_start(self.p_coords, False, False, 0)
        return page

    def sigpad_select(self, d):
        self.p_title.set_markup(f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>")
        self.p_sub.set_text(f"Serial {d['serial'] or '?'}  ·  USB id {d['vid']}:{d['pid']}")
        self._loading = True
        self.p_flip.set_active(bool(self.cfg.get("sigpad_flip", {}).get(norm(d["product"]) or "topaz")))
        self._loading = False
        self.pad_refresh(d)
        self.p_canvas.queue_draw()

    def pad_refresh(self, d):
        s = self.pad_info(d)
        if d["vid"] != "0403":
            text = ("This looks like a USB (HID) Topaz model. Only the serial 'BSB' models are "
                    "supported so far - if you write on it, the pad's own screen still shows the ink.")
        else:
            busy = s["state"].startswith("busy:")
            text = ("⚠ Another program is also reading the pad, so the data may not reach Bench "
                    f"Tested: {s['state'][5:]}. Close it (or another open copy of Bench Tested) and "
                    "unplug/replug the pad.") if busy else {
                "listening": "✔ Listening to the pad.",
                "noperm": "No permission to read the pad. Log out and back in once after installing "
                          "(or run 'sudo usermod -aG dialout $USER').",
                "noport": "The pad's serial port isn't there - unplug it and plug it back in.",
                "gone": "The pad was unplugged.",
            }.get(s["state"], "Connecting…")
        if not HAVE_CAIRO:
            text += "  (Install python3-gi-cairo to see the drawing.)"
        self.p_status.set_text(text)
        cov = self.pad_coverage(s)
        if s["points"]:
            edges = "   ".join(f"{'✓' if cov.get(k) else '○'} {k}" for k in ("left", "right", "top", "bottom"))
            self.p_checks.set_markup(f"<b>✓ Pen writes</b> ({s['points']} points, {len(s['strokes'])} "
                                     f"strokes)     Edges: {edges}")
            last = s["strokes"][-1][-1] if s["strokes"] and s["strokes"][-1] else None
            self.p_coords.set_text(f"Pen at x={last[0]}, y={last[1]}" if last else "")
        else:
            self.p_checks.set_text("Nothing written yet.")
            raw = s.get("raw")
            self.p_coords.set_text(
                f"Data from pad: {raw[0]} bytes received, but not in the expected format "
                f"(latest: {raw[1].hex(' ')})" if raw else
                "Data from pad: nothing received yet - write on the pad.")

    def pad_flip_toggled(self, w):
        d = self.current()
        if self._loading or not d or d["category"] != "sigpad":
            return
        self.cfg.setdefault("sigpad_flip", {})[norm(d["product"]) or "topaz"] = w.get_active()
        save_config(self.cfg)
        self.p_canvas.queue_draw()

    def _pad_xform(self, s, W, H, flip):
        b = s["bounds"]
        bw, bh = max(1, b[2] - b[0]), max(1, b[3] - b[1])
        scale = min((W - 20) / bw, (H - 20) / bh)
        ox = (W - bw * scale) / 2
        oy = (H - bh * scale) / 2

        def f(x, y):
            yy = (b[3] - y) if flip else (y - b[1])
            return ox + (x - b[0]) * scale, oy + yy * scale
        return f, (ox, oy, bw * scale, bh * scale)

    def pad_draw(self, widget, cr):
        d = self.current()
        W, H = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if not d or d["category"] != "sigpad":
            return False
        s = self.pad_info(d)
        f, box = self._pad_xform(s, W, H, self.p_flip.get_active())
        cr.set_source_rgb(0.85, 0.85, 0.85)        # writing area outline
        cr.set_line_width(1)
        cr.rectangle(*box)
        cr.stroke()
        cr.set_source_rgb(0.05, 0.15, 0.55)
        cr.set_line_width(2.2)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        for stroke in s["strokes"]:
            if not stroke:
                continue
            cr.move_to(*f(*stroke[0]))
            for p in stroke[1:]:
                cr.line_to(*f(*p))
            if len(stroke) == 1:
                x, y = f(*stroke[0])
                cr.line_to(x + 0.5, y)
            cr.stroke()
        if s.get("hover"):                     # pen near the pad but not touching
            hx, hy = f(*s["hover"])
            cr.set_source_rgba(0.9, 0.3, 0.1, 0.8)
            cr.arc(hx, hy, 4, 0, 6.2832)
            cr.fill()
        return False

    def pad_clear(self):
        d = self.current()
        if not d:
            return
        s = self.pad_info(d)
        s.update(strokes=[], points=0, seen=None, logged=False, hover=None)
        self.set_row_status(d["key"], "")
        self.pad_refresh(d)
        self.p_canvas.queue_draw()

    def pad_save(self):
        d = self.current()
        if not d:
            return
        s = self.pad_info(d)
        if not s["points"]:
            self.log("Nothing to save yet - sign on the pad first.")
            return
        W, H = 900, 300
        f, box = self._pad_xform(s, W, H, self.p_flip.get_active())
        paths = []
        for stroke in s["strokes"]:
            if stroke:
                pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (f(*p) for p in stroke))
                paths.append(f'<polyline points="{pts}" fill="none" stroke="#0d2690" stroke-width="2.5" '
                             'stroke-linecap="round" stroke-linejoin="round"/>')
        stamp = datetime.datetime.now()
        caption = (f"{display_name(d)}  SN {d['serial'] or '?'}  -  tested "
                   f"{stamp.strftime('%Y-%m-%d %H:%M')}").replace("&", "&amp;").replace("<", "&lt;")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H + 30}">'
               f'<rect width="100%" height="100%" fill="white"/>'
               f'<rect x="{box[0]:.1f}" y="{box[1]:.1f}" width="{box[2]:.1f}" height="{box[3]:.1f}" '
               f'fill="none" stroke="#ccc"/>{"".join(paths)}'
               f'<text x="10" y="{H + 20}" font-family="sans-serif" font-size="14">{caption}</text></svg>')
        os.makedirs(PROOF_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9 ._-]+", "", f"{d['product']} {d['serial']}".strip())
        path = os.path.join(PROOF_DIR, f"{safe} - signature {stamp.strftime('%Y-%m-%d %H.%M.%S')}.svg")
        with open(path, "w") as fh:
            fh.write(svg)
        self.log(f"Saved the signature: {path}")
        # remember the pad's real writing area for this model
        self.cfg.setdefault("sigpad_bounds", {})[norm(d["product"]) or "topaz"] = s["bounds"]
        save_config(self.cfg)
