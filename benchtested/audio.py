"""Record from / play to one specific USB audio device.

Order of preference:
  1. GStreamer + PipeWire (live level meter; works alongside the desktop's sound server)
  2. GStreamer + ALSA hardware device
  3. arecord / aplay command-line tools (no live meter)
It never falls back to the computer's default mic/speakers - a test must only pass
if *this* device made or heard the sound."""

import array
import math
import struct
import wave

from .common import *  # noqa: F401,F403

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
except Exception:  # pragma: no cover
    Gst = None


def alsa_cards(sysdir):
    """ALSA card numbers that belong to this USB device."""
    cards = []
    for c in glob.glob("/sys/class/sound/card*"):
        real = os.path.realpath(c + "/device")
        if real.startswith(sysdir + "/") or real == sysdir:
            m = re.search(r"card(\d+)$", c)
            if m:
                cards.append(int(m.group(1)))
    return sorted(cards)


def pipewire_nodes(cards):
    """{'source': (id, name), 'sink': (id, name)} for the given ALSA cards, from pw-dump."""
    r = run(["pw-dump"], timeout=15)
    if r.returncode != 0:
        return {}
    try:
        objs = json.loads(r.stdout)
    except ValueError:
        return {}
    want = {str(c) for c in cards}
    dev_cards = {}
    for o in objs:
        if o.get("type", "").endswith("Device"):
            p = (o.get("info") or {}).get("props") or {}
            c = p.get("api.alsa.card")
            if c is not None:
                dev_cards[o.get("id")] = str(c)
    out = {}
    for o in objs:
        if not o.get("type", "").endswith(":Node"):
            continue
        p = (o.get("info") or {}).get("props") or {}
        cls = p.get("media.class", "")
        card = p.get("api.alsa.card") or p.get("alsa.card") or p.get("api.alsa.pcm.card") \
            or dev_cards.get(p.get("device.id"))
        if card is None or str(card) not in want:
            continue
        if cls == "Audio/Source" and "source" not in out:
            out["source"] = (o.get("id"), p.get("node.name"))
        elif cls == "Audio/Sink" and "sink" not in out:
            out["sink"] = (o.get("id"), p.get("node.name"))
    return out


def has_capture(card):
    return any(os.path.exists(p) for p in glob.glob(f"/dev/snd/pcmC{card}D*c"))


def has_playback(card):
    return any(os.path.exists(p) for p in glob.glob(f"/dev/snd/pcmC{card}D*p"))


def wav_peak_db(path):
    """Peak level of a 16-bit WAV in dBFS (-inf for silence)."""
    try:
        with wave.open(path) as w:
            if w.getsampwidth() != 2:
                return None
            data = array.array("h", w.readframes(w.getnframes()))
    except (OSError, wave.Error, EOFError):
        return None
    if not data:
        return None
    peak = max(abs(min(data)), abs(max(data)))
    return 20 * math.log10(peak / 32768) if peak else float("-inf")


def write_tone(path, freq=880, secs=1.2, rate=16000, vol=0.35):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        n = int(rate * secs)
        for i in range(n):
            env = min(1.0, i / 800, (n - i) / 800)          # no clicks
            frames += struct.pack("<h", int(32767 * vol * env * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(bytes(frames))


class AudioDevice:
    """One USB device's mic and/or speaker."""

    def __init__(self, sysdir):
        self.cards = alsa_cards(sysdir)
        self.nodes = pipewire_nodes(self.cards) if self.cards else {}
        self.capture_card = next((c for c in self.cards if has_capture(c)), None)
        self.playback_card = next((c for c in self.cards if has_playback(c)), None)

    @property
    def has_mic(self):
        return "source" in self.nodes or self.capture_card is not None

    @property
    def has_speaker(self):
        return "sink" in self.nodes or self.playback_card is not None

    def describe(self):
        parts = []
        if self.cards:
            parts.append("sound card " + ", ".join(str(c) for c in self.cards))
        if self.nodes:
            parts.append("PipeWire " + ", ".join(f"{k}={v[1]}" for k, v in self.nodes.items()))
        return "; ".join(parts) or "no sound device found"

    # ---------------------------------------------------------- GStreamer helpers
    def _target(self, el, which):
        node = self.nodes.get(which)
        if not node:
            return False
        if el.find_property("target-object") is not None:
            el.set_property("target-object", node[1])
        elif el.find_property("path") is not None:
            el.set_property("path", str(node[0]))
        else:
            return False
        return True

    def _src_desc(self):
        """(gst element description, needs_pipewire_target)"""
        if Gst and "source" in self.nodes and Gst.ElementFactory.find("pipewiresrc"):
            return "pipewiresrc name=dev", True
        if Gst and self.capture_card is not None and Gst.ElementFactory.find("alsasrc"):
            return f"alsasrc name=dev device=plughw:{self.capture_card}", False
        return None, False

    def _sink_desc(self):
        if Gst and "sink" in self.nodes and Gst.ElementFactory.find("pipewiresink"):
            return "pipewiresink name=dev", True
        if Gst and self.playback_card is not None and Gst.ElementFactory.find("alsasink"):
            return f"alsasink name=dev device=plughw:{self.playback_card}", False
        return None, False

    def _run_pipeline(self, desc, pw_which, secs=None, on_level=None):
        """Run a pipeline to the end (or for `secs`), from a worker thread. Returns error text or None."""
        try:
            pipe = Gst.parse_launch(desc)
        except Exception as e:
            return str(e)
        if pw_which and not self._target(pipe.get_by_name("dev"), pw_which):
            return "couldn't point the audio at this device"
        bus = pipe.get_bus()
        pipe.set_state(Gst.State.PLAYING)
        start, err, eos_sent = time.time(), None, False
        while True:
            msg = bus.timed_pop(50 * Gst.MSECOND)
            if secs and not eos_sent and time.time() - start >= secs:
                pipe.send_event(Gst.Event.new_eos())
                eos_sent = True
            if time.time() - start > (secs or 30) + 8:
                err = "timed out"
                break
            if msg is None:
                continue
            t = msg.type
            if t == Gst.MessageType.EOS:
                break
            if t == Gst.MessageType.ERROR:
                e, _dbg = msg.parse_error()
                err = e.message
                break
            if t == Gst.MessageType.ELEMENT and on_level:
                s = msg.get_structure()
                if s and s.get_name() == "level":
                    on_level(_peak_from_structure(s))
        pipe.set_state(Gst.State.NULL)
        return err

    # ---------------------------------------------------------- public (call from worker threads)
    def record(self, path, secs=5, on_level=None):
        """Record `secs` seconds to a 16-bit mono WAV. Returns (peak_db, error)."""
        src, pw = self._src_desc()
        if src:
            desc = (f"{src} ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=16000,channels=1 "
                    f"! level interval=60000000 post-messages=true ! wavenc ! filesink location=\"{path}\"")
            err = self._run_pipeline(desc, "source" if pw else None, secs=secs, on_level=on_level)
            if not err:
                return wav_peak_db(path), None
            if not pw or self.capture_card is None:
                return None, err
        if self.capture_card is None:
            return None, "this device has no microphone that Ubuntu can see"
        r = run(["arecord", "-q", "-D", f"plughw:{self.capture_card}", "-f", "S16_LE", "-r", "16000",
                 "-c", "1", "-d", str(int(secs)), path], timeout=secs + 15)
        if r.returncode != 0:
            return None, (r.stderr.strip() or "recording failed")
        return wav_peak_db(path), None

    def play(self, path):
        """Play a WAV through this device's speaker. Returns error text or None."""
        sink, pw = self._sink_desc()
        if sink:
            err = self._run_pipeline(f"filesrc location=\"{path}\" ! wavparse ! audioconvert ! "
                                     f"audioresample ! {sink}", "sink" if pw else None)
            if not err:
                return None
            if not pw or self.playback_card is None:
                return err
        if self.playback_card is None:
            return "this device has no speaker that Ubuntu can see"
        r = run(["aplay", "-q", "-D", f"plughw:{self.playback_card}", path], timeout=60)
        return None if r.returncode == 0 else (r.stderr.strip() or "playback failed")


def play_default(path):
    """Play through the computer's normal speakers/headphones."""
    for cmd in (["pw-play", path], ["paplay", path], ["aplay", "-q", path]):
        if shutil.which(cmd[0]):
            r = run(cmd, timeout=60)
            return None if r.returncode == 0 else (r.stderr.strip() or "playback failed")
    return "no audio player found"


def _peak_from_structure(s):
    try:
        vals = list(s.get_value("peak"))
    except Exception:
        m = re.search(r"peak=\(GValueArray\)<\s*([^>]*)>", s.to_string())
        vals = [float(v) for v in m.group(1).split(",")] if m else []
    return max(vals) if vals else -100.0
