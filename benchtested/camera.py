"""Cameras and conference hubs: live preview, camera controls (incl. pan/tilt/zoom),
timestamped snapshots, and - for devices with audio - a speaker-to-mic loop test."""

import array
import math
import wave

from .common import *  # noqa: F401,F403
from .audio import AudioDevice, Gst, alsa_cards, play_default, write_tone
from .gtkutil import GdkPixbuf, GLib, Gtk, combo, dim, lbl

PROOF_DIR = os.path.expanduser("~/Pictures/Bench Tested")
PTZ = ("pan_absolute", "tilt_absolute", "zoom_absolute")


# ------------------------------------------------------------------ device helpers

def video_nodes(sysdir):
    """/dev/video* capture nodes that belong to this USB device (index 0 = the camera)."""
    nodes = []
    for v in glob.glob("/sys/class/video4linux/video*"):
        real = os.path.realpath(v + "/device")
        if real.startswith(sysdir + "/") and read_file(v + "/index") in ("0", ""):
            nodes.append("/dev/" + os.path.basename(v))
    return sorted(nodes, key=lambda p: int(re.sub(r"\D", "", p) or 0))


def audio_sysdirs(d):
    """Where this camera's sound lives: the camera itself, plus same-brand devices behind
    the same built-in hub (some conference systems are several USB devices in one box)."""
    dirs = [d["sysdir"]]
    parent = os.path.dirname(d["sysdir"])
    parent_vid = read_file(parent + "/idVendor").lower()
    if parent_vid and parent_vid == d["vid"]:          # the hub is part of the same product
        for x in glob.glob("/sys/bus/usb/devices/*"):
            real = os.path.realpath(x)
            if ":" not in os.path.basename(x) and real != d["sysdir"] and os.path.dirname(real) == parent:
                dirs.append(real)
    return dirs


def list_modes(node):
    """[(label, caps)] the camera supports, best first, from v4l2-ctl."""
    r = run(["v4l2-ctl", "-d", node, "--list-formats-ext"], timeout=10)
    modes, fmt = {}, None
    size = None
    for line in r.stdout.splitlines():
        m = re.search(r"\[\d+\]: '(\w+)'", line)
        if m:
            fmt = m.group(1)
            continue
        m = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if m:
            size = (int(m.group(1)), int(m.group(2)))
            continue
        m = re.search(r"\(([\d.]+) fps\)", line)
        if m and size and fmt in ("MJPG", "YUYV", "NV12", "H264"):
            fps = float(m.group(1))
            key = (size, fmt)
            if fps > modes.get(key, 0):
                modes[key] = fps
    out = []
    for (size, fmt), fps in modes.items():
        w, h = size
        if fmt == "MJPG":
            caps = f"image/jpeg,width={w},height={h}"
        elif fmt == "H264":
            continue
        else:
            caps = f"video/x-raw,format={'YUY2' if fmt == 'YUYV' else fmt},width={w},height={h}"
        out.append((w * h, fps, fmt == "MJPG", f"{w} x {h}  ({fps:g} fps, {fmt})", caps))
    out.sort(key=lambda m: (m[0], m[1], m[2]), reverse=True)
    seen, res = set(), []
    for _a, _f, _j, label, caps in out:
        k = label.split("  ")[0]
        if k not in seen:       # one entry per resolution (fastest format)
            seen.add(k)
            res.append((label, caps))
    return res


def list_controls(node):
    """[{name, type, min, max, step, value, default, menu:[(val,label)], inactive}] via v4l2-ctl."""
    r = run(["v4l2-ctl", "-d", node, "--list-ctrls-menus"], timeout=10)
    ctrls, cur = [], None
    for line in r.stdout.splitlines():
        m = re.match(r"\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.*)", line)
        if m:
            name, typ, rest = m.groups()
            kv = dict(re.findall(r"(\w+)=(-?\d+)", rest))
            cur = {"name": name, "type": typ, "menu": [], "inactive": "inactive" in rest,
                   **{k: int(v) for k, v in kv.items() if k in ("min", "max", "step", "default", "value")}}
            ctrls.append(cur)
            continue
        m = re.match(r"\s+(\d+):\s*(.+)", line)
        if m and cur and cur["type"] in ("menu", "intmenu"):
            cur["menu"].append((int(m.group(1)), m.group(2).strip()))
    return [c for c in ctrls if "value" in c]


def set_control(node, name, value):
    r = run(["v4l2-ctl", "-d", node, "-c", f"{name}={value}"], timeout=5)
    return r.returncode == 0


def tone_detected(path, freq=880, rate_hint=16000):
    """Did the recording pick up the test tone? Compares the tone's strength in its
    loudest stretch with the quiet part of the recording (Goertzel filter)."""
    try:
        with wave.open(path) as w:
            rate = w.getframerate()
            data = array.array("h", w.readframes(w.getnframes()))
    except (OSError, wave.Error, EOFError):
        return False, 0.0
    if len(data) < rate // 2:
        return False, 0.0
    win = rate // 10
    k = 2 * math.cos(2 * math.pi * freq / rate)
    powers = []
    for start in range(0, len(data) - win, win):
        s1 = s2 = 0.0
        for x in data[start:start + win]:
            s0 = x + k * s1 - s2
            s2, s1 = s1, s0
        powers.append(s1 * s1 + s2 * s2 - k * s1 * s2)
    if not powers:
        return False, 0.0
    quiet = sorted(powers)[len(powers) // 4] + 1.0
    loud = max(powers)
    ratio_db = 10 * math.log10(loud / quiet)
    level = 10 * math.log10(loud / (win * win * 32768.0 * 32768.0 / 4) + 1e-12)
    return ratio_db > 15 and level > -60, ratio_db


# ================================================================== page

class CameraMixin:
    """Cameras & conference page; mixed into the main window."""

    def camera_init(self):
        self.cam_state = {}
        self.cam_pipe = None
        self.cam_live = None          # key of the camera being previewed
        self.cam_frame = None         # last frame (GdkPixbuf)

    def cam_info(self, d):
        s = self.cam_state.get(d["key"])
        if s is None:
            s = {"node": None, "modes": None, "ctrls": None, "audio": None, "results": {},
                 "mode": None, "frames": 0}
            self.cam_state[d["key"]] = s
        return s

    def camera_devices_changed(self, devs):
        keys = {d["key"] for d in devs if d["category"] == "camera"}
        if self.cam_live and self.cam_live not in keys:
            self.cam_stop()

    # ---------------------------------------------------------- page
    def build_camera_page(self):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(16)
        outer.add(page)
        self.c_title = lbl()
        self.c_sub = dim(lbl(selectable=True))
        self.c_status = lbl(wrap=True)
        for w in (self.c_title, self.c_sub, self.c_status):
            page.pack_start(w, False, False, 0)

        # ---- video
        top = Gtk.Box(spacing=12)
        vleft = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Frame()
        self.c_view = Gtk.Image()
        self.c_view.set_size_request(480, 270)
        frame.add(self.c_view)
        vleft.pack_start(frame, False, False, 0)
        row = Gtk.Box(spacing=6)
        self.c_mode = Gtk.ComboBoxText()
        self.c_mode.set_tooltip_text("Resolution / frame rate")
        self.c_mode.connect("changed", self.cam_mode_changed)
        row.pack_start(self.c_mode, True, True, 0)
        self.c_toggle = Gtk.Button(label="Stop")
        self.c_toggle.connect("clicked", lambda *_: self.cam_toggle())
        row.pack_start(self.c_toggle, False, False, 0)
        snap = Gtk.Button(label="Take photo")
        snap.set_tooltip_text("Save the current picture (with date and time) as proof")
        snap.connect("clicked", lambda *_: self.cam_snapshot())
        row.pack_start(snap, False, False, 0)
        vleft.pack_start(row, False, False, 0)
        self.c_fps = dim(lbl())
        vleft.pack_start(self.c_fps, False, False, 0)
        top.pack_start(vleft, False, False, 0)

        # ---- controls
        cf = Gtk.Frame(label=" Camera controls ")
        cbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cbox.set_border_width(8)
        self.c_ptz = Gtk.Button(label="Test pan / tilt / zoom")
        self.c_ptz.set_tooltip_text("Moves the camera through its pan, tilt and zoom range")
        self.c_ptz.connect("clicked", lambda *_: self.current() and
                           self.task(self.current(), self.cam_ptz_test, self.current()))
        cbox.pack_start(self.c_ptz, False, False, 0)
        csw = Gtk.ScrolledWindow()
        csw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        csw.set_size_request(300, 260)
        self.c_ctrl_grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        csw.add(self.c_ctrl_grid)
        cbox.pack_start(csw, True, True, 0)
        reset = Gtk.Button(label="Reset controls to default")
        reset.connect("clicked", lambda *_: self.cam_reset_controls())
        cbox.pack_start(reset, False, False, 0)
        cf.add(cbox)
        top.pack_start(cf, True, True, 0)
        page.pack_start(top, False, False, 0)

        # ---- audio
        self.c_audio = Gtk.Frame(label=" Audio (conference hubs, webcams with a mic) ")
        ab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ab.set_border_width(10)
        self.c_audio.add(ab)
        self.c_audio_info = lbl(wrap=True)
        ab.pack_start(self.c_audio_info, False, False, 0)
        self.c_level = Gtk.LevelBar()
        self.c_level.set_min_value(0)
        self.c_level.set_max_value(1)
        self.c_level.set_size_request(-1, 16)
        ab.pack_start(self.c_level, False, False, 0)
        arow = Gtk.Box(spacing=8)
        self.c_loop = Gtk.Button(label="Speaker → mic check")
        self.c_loop.get_style_context().add_class("suggested-action")
        self.c_loop.set_tooltip_text("Plays a tone through the device's speaker and listens for it "
                                     "with the device's own mic - checks both at once")
        self.c_loop.connect("clicked", lambda *_: self.current() and
                            self.task(self.current(), self.cam_loop_test, self.current()))
        arow.pack_start(self.c_loop, True, True, 0)
        self.c_rec = Gtk.Button(label="Record & play back (3 s)")
        self.c_rec.connect("clicked", lambda *_: self.current() and
                           self.task(self.current(), self.cam_record_test, self.current()))
        arow.pack_start(self.c_rec, True, True, 0)
        ab.pack_start(arow, False, False, 0)
        self.c_audio_res = lbl(markup=True, wrap=True)
        ab.pack_start(self.c_audio_res, False, False, 0)
        page.pack_start(self.c_audio, False, False, 0)
        for w in (self.c_ptz, self.c_loop, self.c_rec):
            self.page_widgets.append(w)
        return outer

    def camera_select(self, d):
        s = self.cam_info(d)
        self.c_title.set_markup(f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>")
        self.c_sub.set_text(f"Serial {d['serial'] or '?'}  ·  USB id {d['vid']}:{d['pid']}")
        self.c_view.clear()
        self.c_fps.set_text("")
        self.c_audio_res.set_text("")
        self.cam_render_results(d)
        if s["modes"] is None:
            self.c_status.set_text("Connecting to the camera…")
            s["modes"] = []

            def _load():
                nodes = []
                for _ in range(6):                     # the video node can appear a moment after USB
                    nodes = video_nodes(d["sysdir"])
                    if nodes:
                        break
                    time.sleep(0.5)
                s["node"] = nodes[0] if nodes else None
                if s["node"]:
                    s["modes"] = list_modes(s["node"])
                    s["ctrls"] = list_controls(s["node"])
                a = None
                for sd in audio_sysdirs(d):
                    if alsa_cards(sd):
                        a = AudioDevice(sd)
                        break
                s["audio"] = a or False
                GLib.idle_add(lambda: (d["key"] == self.selected_key and self.camera_select(d), False)[1])
            self.in_thread(_load)
            return
        if not s["node"]:
            self.c_status.set_text("Ubuntu doesn't show a camera for this device yet. Unplug it and plug it "
                                   "back in; some conference cameras need their own setup app first.")
        elif Gst is None:
            self.c_status.set_text("Live preview needs GStreamer: sudo apt install gir1.2-gstreamer-1.0 "
                                   "gstreamer1.0-plugins-good")
        else:
            self.c_status.set_text(f"✔ Camera found ({s['node']}).")
        self._loading = True
        self.c_mode.remove_all()
        if s["modes"]:
            for label, caps in s["modes"]:
                self.c_mode.append(caps, label)
            pick = s["mode"] or next((c for lab, c in s["modes"] if lab.startswith("1280 x 720")),
                                     s["modes"][0][1])
            self.c_mode.set_active_id(pick)
        else:
            self.c_mode.append("", "Default")
            self.c_mode.set_active_id("")
        self._loading = False
        self.cam_build_controls(d)
        a = s["audio"]
        if a:
            bits = [x for x, ok in (("microphone", a.has_mic), ("speaker", a.has_speaker)) if ok]
            self.c_audio_info.set_text("This device has a " + " and a ".join(bits) + "." if bits else
                                       "No microphone or speaker found on this device.")
            self.c_audio.set_sensitive(True)
            self.c_loop.set_sensitive(a.has_mic and a.has_speaker)
            self.c_rec.set_sensitive(a.has_mic)
        else:
            self.c_audio_info.set_text("This camera has no microphone or speaker - the audio tests "
                                       "don't apply." if a is False else "Checking for audio…")
            self.c_audio.set_sensitive(False)
        if s["node"] and Gst is not None:
            self.cam_start(d)

    # ---------------------------------------------------------- live preview
    def cam_mode_changed(self, w):
        d = self.current()
        if self._loading or not d or d["category"] != "camera":
            return
        self.cam_info(d)["mode"] = w.get_active_id()
        self.cam_start(d)

    def cam_toggle(self):
        d = self.current()
        if self.cam_pipe:
            self.cam_stop()
            self.c_toggle.set_label("Start")
        elif d and d["category"] == "camera":
            self.cam_start(d)

    def cam_stop(self):
        if self.cam_pipe:
            self.cam_pipe.set_state(Gst.State.NULL)
            self.cam_pipe = None
        self.cam_live = None

    def cam_start(self, d):
        self.cam_stop()
        s = self.cam_info(d)
        caps = self.c_mode.get_active_id() or ""
        decode = "jpegdec ! " if caps.startswith("image/jpeg") else ""
        stamp = ""
        if Gst.ElementFactory.find("clockoverlay"):
            stamp = ('clockoverlay time-format="%Y-%m-%d %H:%M:%S" halignment=left valignment=bottom '
                     'font-desc="Sans 14" shaded-background=true ! ')
        desc = (f"v4l2src device={s['node']} ! {caps + ' ! ' if caps else ''}{decode}videoconvert ! "
                f"{stamp}videoconvert ! video/x-raw,format=RGB ! appsink name=sink max-buffers=1 drop=true "
                "sync=false emit-signals=false")
        try:
            pipe = Gst.parse_launch(desc)
        except Exception as e:
            self.log(f"Couldn't start the preview: {e}")
            return
        self.cam_pipe, self.cam_live = pipe, d["key"]
        s["frames"], s["t0"] = 0, time.time()
        sink = pipe.get_by_name("sink")
        bus = pipe.get_bus()
        pipe.set_state(Gst.State.PLAYING)
        self.c_toggle.set_label("Stop")

        def _pull():
            while self.cam_pipe is pipe:
                msg = bus.pop_filtered(Gst.MessageType.ERROR)
                if msg:
                    err, _dbg = msg.parse_error()
                    GLib.idle_add(self._cam_error, d, err.message)
                    return
                sample = sink.emit("try-pull-sample", 200 * Gst.MSECOND)
                if sample is None:
                    continue
                st = sample.get_caps().get_structure(0)
                w, h = st.get_value("width"), st.get_value("height")
                buf = sample.get_buffer()
                ok, info = buf.map(Gst.MapFlags.READ)
                if not ok:
                    continue
                data = bytes(info.data)
                buf.unmap(info)
                stride = (w * 3 + 3) // 4 * 4
                GLib.idle_add(self._cam_show, d["key"], data, w, h, stride)
        threading.Thread(target=_pull, daemon=True).start()

    def _cam_error(self, d, text):
        self.log(f"[{short_name(d)}] camera error: {text}")
        self.c_status.set_text("⚠ The camera stopped: " + text + "  Try another resolution, or unplug "
                               "and replug it.")
        self.cam_stop()
        self.c_toggle.set_label("Start")
        return False

    def _cam_show(self, key, data, w, h, stride):
        if key != self.cam_live or key != self.selected_key:
            return False
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                             False, 8, w, h, stride)
        self.cam_frame = pb
        tw = 480
        th = max(1, int(h * tw / w))
        self.c_view.set_from_pixbuf(pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR))
        d = self.device(key)
        s = self.cam_info(d) if d else None
        if s is not None:
            s["frames"] += 1
            if s["frames"] == 1:
                s["results"]["video"] = True
                self.log(f"[{short_name(d)}] picture ✓ ({w} x {h})")
                self.set_row_status(key, self.cam_summary(d))
                self.cam_render_results(d)
            el = time.time() - s["t0"]
            if s["frames"] % 15 == 0 and el > 0:
                self.c_fps.set_text(f"{w} x {h}  ·  about {s['frames'] / el:.0f} frames per second")
        return False

    def cam_snapshot(self):
        d = self.current()
        if not d or self.cam_frame is None:
            self.log("No picture yet.")
            return
        os.makedirs(PROOF_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        safe = re.sub(r"[^A-Za-z0-9 ._-]+", "", f"{d['product']} {d['serial']}".strip())
        path = os.path.join(PROOF_DIR, f"{safe} - photo {stamp}.png")
        try:
            self.cam_frame.savev(path, "png", [], [])
            self.log(f"Saved photo: {path}")
        except Exception as e:
            self.log(f"Couldn't save the photo: {e}")

    # ---------------------------------------------------------- controls
    def cam_build_controls(self, d):
        for child in self.c_ctrl_grid.get_children():
            self.c_ctrl_grid.remove(child)
        s = self.cam_info(d)
        ctrls = s["ctrls"] or []
        has_ptz = any(c["name"] in PTZ for c in ctrls)
        self.c_ptz.set_visible(has_ptz)
        if not ctrls:
            self.c_ctrl_grid.attach(dim(lbl("This camera has no adjustable controls.")), 0, 0, 2, 1)
            self.c_ctrl_grid.show_all()
            return
        order = sorted(ctrls, key=lambda c: (c["name"] not in PTZ, c["name"]))
        for r, c in enumerate(order):
            name = c["name"].replace("_", " ").replace("absolute", "").strip().capitalize()
            self.c_ctrl_grid.attach(lbl(name), 0, r, 1, 1)
            if c["type"] == "bool":
                w = Gtk.Switch()
                w.set_active(bool(c["value"]))
                w.set_halign(Gtk.Align.START)
                w.connect("notify::active", lambda sw, _p, n=c["name"]: self.cam_set(n, int(sw.get_active())))
            elif c["type"] in ("menu", "intmenu") and c["menu"]:
                w = combo([(str(v), t) for v, t in c["menu"]], str(c["value"]))
                w.connect("changed", lambda cb, n=c["name"]: self.cam_set(n, cb.get_active_id()))
            elif c["type"] in ("int", "int64") and "min" in c and "max" in c and c["max"] > c["min"]:
                w = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, c["min"], c["max"], max(1, c.get("step", 1)))
                w.set_value(c["value"])
                w.set_size_request(160, -1)
                w.connect("value-changed", lambda sc, n=c["name"]: self.cam_set(n, int(sc.get_value())))
            else:
                continue
            w.set_sensitive(not c["inactive"])
            self.c_ctrl_grid.attach(w, 1, r, 1, 1)
        self.c_ctrl_grid.show_all()

    def cam_set(self, name, value):
        d = self.current()
        if self._loading or not d or d["category"] != "camera":
            return
        node = self.cam_info(d)["node"]
        if node and not set_control(node, name, value):
            self.log(f"The camera didn't accept {name} = {value} (some settings are locked while "
                     "'auto' is on).")

    def cam_reset_controls(self):
        d = self.current()
        if not d:
            return
        s = self.cam_info(d)
        for c in s["ctrls"] or []:
            if "default" in c and not c["inactive"]:
                set_control(s["node"], c["name"], c["default"])
        s["ctrls"] = list_controls(s["node"]) if s["node"] else []
        self._loading = True
        self.cam_build_controls(d)
        self._loading = False
        self.log("Camera controls reset to their defaults.")

    def cam_ptz_test(self, d):
        s = self.cam_info(d)
        node = s["node"]
        ctrls = {c["name"]: c for c in (s["ctrls"] or [])}
        moved = []
        for name in PTZ:
            c = ctrls.get(name)
            if not c or "min" not in c:
                continue
            label = name.split("_")[0]
            self.log(f"Moving: {label}…")
            lo, hi, home = c["min"], c["max"], c.get("default", c["value"])
            if name == "zoom_absolute":
                seq = [hi, lo, home]
            else:
                span = (hi - lo) // 3
                seq = [home - span, home + span, home]
            ok = True
            for v in seq:
                ok = set_control(node, name, clamp(v, lo, hi)) and ok
                time.sleep(1.6)
            moved.append(f"{label} {'✓' if ok else '✗'}")
        if not moved:
            self.log("This camera has no pan/tilt/zoom.")
            return False
        s["results"]["ptz"] = all("✓" in m for m in moved)
        self.log("Pan/tilt/zoom: " + ", ".join(moved) + ". Did the picture move each time?")
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.cam_render_results(d), False)[1])
        return self.cam_summary(d)

    # ---------------------------------------------------------- audio
    def cam_loop_test(self, d):
        a = self.cam_info(d)["audio"]
        if not a or not (a.has_mic and a.has_speaker):
            self.log("This device needs both a speaker and a microphone for this check.")
            return False
        tone = os.path.join(tempfile.gettempdir(), "bench-tested-loop-tone.wav")
        rec = os.path.join(tempfile.gettempdir(), "bench-tested-loop-rec.wav")
        write_tone(tone, freq=880, secs=1.2, vol=0.5)
        self.log("Speaker → mic check: playing a tone and listening for it…")
        result = {}

        def _record():
            result["peak"], result["err"] = a.record(rec, 3, on_level=self._cam_level)
        t = threading.Thread(target=_record, daemon=True)
        t.start()
        time.sleep(0.8)
        perr = a.play(tone)
        t.join(15)
        GLib.idle_add(lambda: (self.c_level.set_value(0), False)[1])
        if perr:
            self.log("Couldn't play the tone on the device's speaker: " + perr)
        if result.get("err"):
            self.log("Couldn't record from the device's mic: " + result["err"])
            return False
        heard, margin = tone_detected(rec)
        s = self.cam_info(d)
        s["results"]["loop"] = heard
        self.log(("✓ The device's mic heard its own speaker" if heard else
                  "✗ The tone wasn't picked up - check the speaker volume/mute, then try again") +
                 f" (tone {margin:.0f} dB above the background).")
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.cam_render_results(d), False)[1])
        return self.cam_summary(d)

    def cam_record_test(self, d):
        a = self.cam_info(d)["audio"]
        if not a or not a.has_mic:
            self.log("This device has no microphone.")
            return False
        rec = os.path.join(tempfile.gettempdir(), "bench-tested-cam-rec.wav")
        self.log("Recording for 3 seconds - talk toward the device…")
        peak, err = a.record(rec, 3, on_level=self._cam_level)
        GLib.idle_add(lambda: (self.c_level.set_value(0), False)[1])
        if err:
            self.log("Recording failed: " + err)
            return False
        ok = peak is not None and peak > -45
        self.cam_info(d)["results"]["mic"] = ok
        self.log(f"Mic {'picked up sound ✓' if ok else 'recorded only silence ✗'} (peak {peak or -100:.0f} dB). "
                 "Playing it back…")
        err = a.play(rec) if a.has_speaker else play_default(rec)
        if err:
            self.log("Playback failed: " + err)
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.cam_render_results(d), False)[1])
        return self.cam_summary(d)

    def _cam_level(self, db):
        GLib.idle_add(lambda: (self.c_level.set_value(clamp((db + 60) / 60, 0, 1)), False)[1])

    def cam_summary(self, d):
        r = self.cam_info(d)["results"]
        parts = []
        for k, name in (("video", "picture"), ("ptz", "PTZ"), ("loop", "speaker+mic"), ("mic", "mic")):
            if k in r:
                parts.append(f"{name} {'✓' if r[k] else '✗'}")
        return "  ·  ".join(parts)

    def cam_render_results(self, d):
        r = self.cam_info(d)["results"]
        if "loop" in r:
            self.c_audio_res.set_markup("<b>Speaker → mic ✓</b> the mic heard the speaker." if r["loop"] else
                                        "<b>Speaker → mic ✗</b> the tone wasn't heard.")
        elif "mic" in r:
            self.c_audio_res.set_markup("<b>Mic ✓</b>" if r["mic"] else "<b>Mic ✗</b> only silence.")
