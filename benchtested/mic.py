"""Dictation microphones (Nuance PowerMic, Philips SpeechMike, ...): record and play
back through the mic's own speaker, a press-each-button checklist, trackball checks."""

from .common import *  # noqa: F401,F403
from .audio import AudioDevice, play_default, write_tone
from .gtkutil import GLib, Gtk, dim, lbl
from .inputdev import (BTN_LEFT, BTN_MIDDLE, BTN_RIGHT, EV_KEY, EV_REL, HidrawWatcher, InputWatcher,
                       event_nodes, hidraw_nodes)

RAW_BASE = 100000      # buttons found in raw HID reports are numbered from here

TRACKBALL_VENDORS = {"df04"}       # PowerMic's built-in trackball shows up as its own USB device

BTN_NAMES = {BTN_LEFT: "Left click", BTN_RIGHT: "Right click", BTN_MIDDLE: "Middle click"}


def find_trackball(d):
    """The trackball that sits behind the same built-in hub as the mic (PowerMic II/III)."""
    parent = os.path.dirname(d["sysdir"])
    for x in glob.glob("/sys/bus/usb/devices/*"):
        if ":" in os.path.basename(x):
            continue
        real = os.path.realpath(x)
        if real != d["sysdir"] and os.path.dirname(real) == parent and \
                read_file(x + "/idVendor").lower() in TRACKBALL_VENDORS:
            return real
    return None


def button_name(code):
    if code >= RAW_BASE:
        return f"Button {code - RAW_BASE}"
    if code in BTN_NAMES:
        return BTN_NAMES[code]
    if 0x100 <= code < 0x110:
        return f"Button {code - 0xFF}"
    if 0x120 <= code < 0x130:
        return f"Button {code - 0x120 + 17}"
    return f"Key {code}"


class MicMixin:
    """Dictation-mic page; mixed into the main window."""

    def mic_init(self):
        self.mic_state = {}

    def mic_info(self, d):
        s = self.mic_state.get(d["key"])
        if s is None:
            s = {"peak": None, "speaker": None, "buttons": set(), "ball": set(), "watchers": [],
                 "input": "starting", "raw": "starting", "src": None, "recording": None, "audio": None}
            self.mic_state[d["key"]] = s
        return s

    # ---------------------------------------------------------- follow the device list
    def mic_devices_changed(self, devs):
        mics = {d["key"]: d for d in devs if d["category"] == "mic"}
        for key in list(self.mic_state):
            st = self.mic_state[key]
            if key not in mics or st.get("plug") != mics[key].get("plug"):
                # unplugged, or a different/replugged mic in the same port: start over
                for w in st["watchers"]:
                    w.stop()
                del self.mic_state[key]
                self.set_row_status(key, "")
        for key, d in mics.items():
            s = self.mic_info(d)
            s["plug"] = d.get("plug")
            if not s["watchers"]:
                nodes = event_nodes(d["sysdir"])
                tb = find_trackball(d)
                d["trackball"] = tb
                if tb:
                    nodes += event_nodes(tb)
                s["watchers"].append(InputWatcher(
                    nodes, lambda t, c, v, k=key: GLib.idle_add(self.mic_event, k, t, c, v),
                    lambda st, k=key: GLib.idle_add(self.mic_input_state, k, st)))
                # buttons that only show up as raw HID reports (SpeechMike and many others)
                s["watchers"].append(HidrawWatcher(
                    hidraw_nodes(d["sysdir"]), lambda b, k=key: GLib.idle_add(self.mic_raw_press, k, b),
                    lambda st, k=key: GLib.idle_add(self.mic_raw_state, k, st)))
            else:
                d["trackball"] = find_trackball(d)

    def mic_input_state(self, key, st):
        d = self.device(key)
        if d:
            self.mic_info(d)["input"] = st
            if st == "gone":                     # its connection went away - reconnect next time
                for w in self.mic_info(d)["watchers"]:
                    w.stop()
                self.mic_info(d)["watchers"] = []
            if key == self.selected_key:
                self.mic_refresh_page(d)
        return False

    def mic_raw_state(self, key, st):
        d = self.device(key)
        if d:
            self.mic_info(d)["raw"] = st
            if key == self.selected_key:
                self.mic_refresh_page(d)
        return False

    def mic_raw_press(self, key, n):
        d = self.device(key)
        if not d:
            return False
        s = self.mic_info(d)
        if s["src"] == "evdev":
            return False              # this mic reports buttons normally; raw reports would double up
        s["src"] = "raw"
        return self.mic_event(key, EV_KEY, RAW_BASE + n, 1)

    def mic_event(self, key, t, code, value):
        d = self.device(key)
        if not d:
            return False
        s = self.mic_info(d)
        changed = False
        if t == EV_KEY and value == 1:
            if code in BTN_NAMES and d.get("trackball"):
                s["ball"].add(code)
            else:
                if code < RAW_BASE and s["src"] != "evdev":
                    s["src"] = "evdev"          # normal button events win over raw reports
                    s["buttons"] = {c for c in s["buttons"] if c < RAW_BASE}
                changed = code not in s["buttons"]
                s["buttons"].add(code)
            if key == self.selected_key:
                self.m_last.set_text(f"Last pressed: {button_name(code)}")
        elif t == EV_REL and "move" not in s["ball"]:
            s["ball"].add("move")
            changed = True
        if changed and code not in BTN_NAMES:
            self.log(f"[{short_name(d)}] {button_name(code)} works" if t == EV_KEY else
                     f"[{short_name(d)}] trackball moves")
        self.mic_row_status(d)
        if key == self.selected_key:
            self.mic_refresh_page(d)
        return False

    def mic_summary(self, d):
        s = self.mic_info(d)
        parts = []
        if s["peak"] is not None:
            parts.append("mic ✓" if s["peak"] > -45 else "mic ✗")
        if s["speaker"] is not None:
            parts.append("speaker ✓" if s["speaker"] else "speaker ✗")
        if s["buttons"]:
            parts.append(f"{len(s['buttons'])} buttons ✓")
        if s["ball"]:
            parts.append("trackball ✓")
        return "  ·  ".join(parts)

    def mic_row_status(self, d):
        self.set_row_status(d["key"], self.mic_summary(d))

    # ---------------------------------------------------------- page
    def build_mic_page(self):
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_border_width(16)
        outer.add(page)
        self.m_title = lbl()
        self.m_sub = dim(lbl(selectable=True))
        page.pack_start(self.m_title, False, False, 0)
        page.pack_start(self.m_sub, False, False, 0)

        # ---- mic & speaker
        f1 = Gtk.Frame(label=" Microphone and speaker ")
        b1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b1.set_border_width(10)
        f1.add(b1)
        b1.pack_start(lbl("Click <b>Record &amp; play back</b>, talk into the mic for 3 seconds, "
                          "then listen: your recording plays through the mic's own speaker - "
                          "one test for both.", markup=True, wrap=True), False, False, 0)
        self.m_level = Gtk.LevelBar()
        self.m_level.set_min_value(0)
        self.m_level.set_max_value(1)
        self.m_level.set_size_request(-1, 18)
        b1.pack_start(self.m_level, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.m_rec = Gtk.Button(label="Record & play back (3 s)")
        self.m_rec.get_style_context().add_class("suggested-action")
        self.m_rec.connect("clicked", lambda *_: self.current() and
                           self.task(self.current(), self.mic_record_test, self.current()))
        row.pack_start(self.m_rec, True, True, 0)
        self.m_tone = Gtk.Button(label="Test tone on mic speaker")
        self.m_tone.connect("clicked", lambda *_: self.current() and
                            self.task(self.current(), self.mic_tone, self.current()))
        row.pack_start(self.m_tone, True, True, 0)
        self.m_pc = Gtk.Button(label="Play recording on computer")
        self.m_pc.set_tooltip_text("Play the last recording through your computer's speakers instead")
        self.m_pc.connect("clicked", lambda *_: self.current() and
                          self.task(self.current(), self.mic_play_pc, self.current()))
        row.pack_start(self.m_pc, True, True, 0)
        b1.pack_start(row, False, False, 0)
        self.m_mic_res = lbl(markup=True, wrap=True)
        b1.pack_start(self.m_mic_res, False, False, 0)
        self.m_heard = Gtk.Box(spacing=8)
        self.m_heard.pack_start(lbl("Did you hear it from the mic's speaker?"), False, False, 0)
        for text, val in (("Yes, heard it", True), ("No sound", False)):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda _b, v=val: self.mic_heard(v))
            self.m_heard.pack_start(b, False, False, 0)
        b1.pack_start(self.m_heard, False, False, 0)
        self.m_spk_res = lbl(markup=True)
        b1.pack_start(self.m_spk_res, False, False, 0)
        page.pack_start(f1, False, False, 0)

        # ---- buttons
        f2 = Gtk.Frame(label=" Buttons ")
        b2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b2.set_border_width(10)
        f2.add(b2)
        b2.pack_start(lbl("Press every button on the mic once. Each one that works is added below.",
                          wrap=True), False, False, 0)
        self.m_btn_flow = Gtk.FlowBox()
        self.m_btn_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.m_btn_flow.set_max_children_per_line(6)
        b2.pack_start(self.m_btn_flow, False, False, 0)
        self.m_btn_count = lbl(markup=True)
        b2.pack_start(self.m_btn_count, False, False, 0)
        self.m_last = dim(lbl())
        b2.pack_start(self.m_last, False, False, 0)
        page.pack_start(f2, False, False, 0)

        # ---- trackball
        self.m_ball_frame = Gtk.Frame(label=" Trackball ")
        b3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b3.set_border_width(10)
        self.m_ball_frame.add(b3)
        b3.pack_start(lbl("Roll the trackball and click both trackball buttons.", wrap=True), False, False, 0)
        self.m_ball = lbl(markup=True)
        b3.pack_start(self.m_ball, False, False, 0)
        page.pack_start(self.m_ball_frame, False, False, 0)

        self.m_note = dim(lbl(wrap=True))
        page.pack_start(self.m_note, False, False, 0)
        reset = Gtk.Button(label="Start this mic's test over")
        reset.connect("clicked", lambda *_: self.mic_reset())
        page.pack_start(reset, False, False, 0)
        for w in (self.m_rec, self.m_tone, self.m_pc):
            self.page_widgets.append(w)
        return outer

    def mic_select(self, d):
        self.m_title.set_markup(f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>")
        self.m_sub.set_text(f"Serial {d['serial'] or '?'}  ·  USB id {d['vid']}:{d['pid']}")
        self.m_level.set_value(0)
        self.mic_refresh_page(d)
        s = self.mic_info(d)
        if s["audio"] is None:
            def _load():
                a = AudioDevice(d["sysdir"])
                s["audio"] = a
                self.log(f"[{short_name(d)}] audio: {a.describe()}")
                GLib.idle_add(lambda: (d["key"] == self.selected_key and self.mic_refresh_page(d), False)[1])
            self.in_thread(_load)

    def mic_refresh_page(self, d):
        s = self.mic_info(d)
        a = s["audio"]
        if s["peak"] is None:
            self.m_mic_res.set_text("Microphone: not tested yet.")
        elif s["peak"] > -45:
            self.m_mic_res.set_markup(f"<b>Microphone ✓</b>  picked up sound (peak {s['peak']:.0f} dB).")
        else:
            self.m_mic_res.set_markup("<b>Microphone ✗</b>  recorded only silence. Check the mic's "
                                      "mute/record button and try again.")
        self.m_heard.set_visible(bool(s["recording"]) and s["speaker"] is None)
        self.m_spk_res.set_markup("" if s["speaker"] is None else
                                  "<b>Speaker ✓</b>  you heard it." if s["speaker"] else
                                  "<b>Speaker ✗</b>  no sound from the mic's speaker.")
        for child in self.m_btn_flow.get_children():
            self.m_btn_flow.remove(child)
        for code in sorted(s["buttons"]):
            self.m_btn_flow.add(lbl(f"✓ {button_name(code)}"))
        self.m_btn_flow.show_all()
        n = len(s["buttons"])
        self.m_btn_count.set_markup(f"<b>{n} button{'s' if n != 1 else ''} working</b>" if n
                                    else "No buttons pressed yet.")
        tb = d.get("trackball")
        self.m_ball_frame.set_visible(bool(tb))
        if tb:
            ball = s["ball"]
            self.m_ball.set_markup("   ".join(f"{'✓' if k in ball else '○'} {name}" for k, name in
                                              (("move", "Rolls"), (BTN_LEFT, "Left click"),
                                               (BTN_RIGHT, "Right click"))))
        notes = []
        if s["raw"] == "noperm":
            notes.append("Some buttons may not register until the mic is unplugged and plugged back in "
                         "after installing (the installer gives the app access to dictation mics).")
        if s["input"] == "noperm":
            notes.append("Buttons can't be read yet: log out and back in once after installing "
                         "(or run 'sudo usermod -aG input $USER').")
        if a is not None and not a.has_mic:
            notes.append("Ubuntu doesn't see a microphone on this device.")
        if a is not None and not a.has_speaker:
            notes.append("This device has no speaker - use 'Play recording on computer' to listen.")
        self.m_note.set_text("  ".join(notes))
        self.m_tone.set_sensitive(a is None or a.has_speaker)

    def _mic_audio(self, d):
        s = self.mic_info(d)
        if s["audio"] is None:
            s["audio"] = AudioDevice(d["sysdir"])
        return s["audio"]

    def mic_record_test(self, d):
        a = self._mic_audio(d)
        s = self.mic_info(d)
        if not a.has_mic:
            self.log("Ubuntu doesn't see a microphone on this device.")
            return False
        safe = re.sub(r"\W", "", d["key"])
        path = os.path.join(tempfile.gettempdir(), f"bench-tested-mic-{safe}.wav")
        self.log("Recording for 3 seconds - talk into the mic now…")

        def level(db):
            GLib.idle_add(lambda: (d["key"] == self.selected_key and
                                   self.m_level.set_value(clamp((db + 60) / 60, 0, 1)), False)[1])
        peak, err = a.record(path, 3, on_level=level)
        GLib.idle_add(lambda: (self.m_level.set_value(0), False)[1])
        if err:
            self.log("Recording failed: " + err)
            return False
        s["peak"] = peak if peak is not None else -100.0
        s["recording"] = path
        ok = s["peak"] > -45
        self.log(f"Recorded. Peak level {s['peak']:.0f} dB - " +
                 ("the mic picked up sound ✓" if ok else "only silence ✗"))
        if a.has_speaker:
            self.log("Playing it back through the mic's speaker…")
            err = a.play(path)
            if err:
                self.log("Playback on the mic's speaker failed: " + err)
        else:
            self.log("This device has no speaker - playing it on the computer instead.")
            play_default(path)
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.mic_refresh_page(d), False)[1])
        return self.mic_summary(d)

    def mic_tone(self, d):
        a = self._mic_audio(d)
        path = os.path.join(tempfile.gettempdir(), "bench-tested-tone.wav")
        write_tone(path)
        self.log("Playing a test tone on the mic's speaker…")
        err = a.play(path)
        if err:
            self.log("Couldn't play the tone: " + err)
            return False
        s = self.mic_info(d)
        if s["recording"] is None:
            s["recording"] = "tone"
        GLib.idle_add(lambda: (d["key"] == self.selected_key and self.mic_refresh_page(d), False)[1])
        return self.mic_summary(d) or "tone played - did you hear it?"

    def mic_play_pc(self, d):
        rec = self.mic_info(d)["recording"]
        if not rec or rec == "tone" or not os.path.exists(rec):
            self.log("Record something first.")
            return False
        err = play_default(rec)
        if err:
            self.log("Couldn't play it: " + err)
        return self.mic_summary(d) if not err else False

    def mic_heard(self, heard):
        d = self.current()
        if not d:
            return
        s = self.mic_info(d)
        s["speaker"] = heard
        self.log(f"[{short_name(d)}] speaker " + ("works ✓" if heard else "- no sound ✗"))
        self.mic_row_status(d)
        self.mic_refresh_page(d)

    def mic_reset(self):
        d = self.current()
        if not d:
            return
        s = self.mic_info(d)
        s.update(peak=None, speaker=None, buttons=set(), ball=set(), recording=None, src=None)
        self.set_row_status(d["key"], "")
        self.mic_refresh_page(d)
