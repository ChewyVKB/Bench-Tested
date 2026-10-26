"""Read raw button/movement events from a USB device's /dev/input nodes."""

import fcntl
import select
import struct

from .common import *  # noqa: F401,F403

EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EVIOCGRAB = 0x40044590
EV_KEY, EV_REL = 1, 2
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112


def event_nodes(sysdir):
    """All /dev/input/event* nodes that belong to a USB device."""
    nodes = []
    for ev in glob.glob("/sys/class/input/event*"):
        real = os.path.realpath(ev + "/device")
        if real.startswith(sysdir + "/"):
            nodes.append("/dev/input/" + os.path.basename(ev))
    return sorted(nodes, key=lambda p: int(re.sub(r"\D", "", p) or 0))


class InputWatcher:
    """Calls on_event(type, code, value) for every event from the given nodes.
    on_state(state) gets 'listening', 'noperm', 'none' or 'gone'."""

    def __init__(self, nodes, on_event, on_state, grab=False):
        self.nodes, self.on_event, self.on_state, self.grab = nodes, on_event, on_state, grab
        self.stop_flag = False
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag = True

    def _run(self):
        fds = []
        for n in self.nodes:
            try:
                fd = os.open(n, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                for f in fds:
                    os.close(f)
                self.on_state("noperm")
                return
            except OSError:
                continue
            if self.grab:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                except OSError:
                    pass
            fds.append(fd)
        if not fds:
            self.on_state("none")
            return
        self.on_state("listening")
        try:
            while not self.stop_flag:
                r, _w, _x = select.select(fds, [], [], 0.2)
                for fd in r:
                    try:
                        data = os.read(fd, EVENT_SIZE * 64)
                    except OSError:
                        self.on_state("gone")
                        return
                    for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                        _s, _us, t, code, value = struct.unpack(EVENT_FMT, data[i:i + EVENT_SIZE])
                        if t in (EV_KEY, EV_REL):
                            self.on_event(t, code, value)
        finally:
            for fd in fds:
                if self.grab:
                    try:
                        fcntl.ioctl(fd, EVIOCGRAB, 0)
                    except OSError:
                        pass
                os.close(fd)


# ------------------------------------------------------------------ raw HID reports
# Many dictation mics (Philips SpeechMike, newer Nuance models, ...) send their buttons as
# vendor-specific HID reports that Linux doesn't turn into key events. Reading /dev/hidraw*
# directly and watching which values change catches those buttons on any model.

def hidraw_nodes(sysdir):
    """/dev/hidraw* of this USB device, skipping mouse/trackball interfaces."""
    nodes = []
    for h in glob.glob("/sys/class/hidraw/hidraw*"):
        dev = os.path.realpath(h + "/device")
        if not dev.startswith(sysdir + "/"):
            continue
        rel = [read_file(c) for c in glob.glob(dev + "/input/input*/capabilities/rel")]
        if any(r.strip("0 ") for r in rel):
            continue                       # it's a pointer (trackball) - handled as a mouse
        nodes.append("/dev/" + os.path.basename(h))
    return sorted(nodes, key=lambda p: int(re.sub(r"\D", "", p) or 0))


class RawButtons:
    """Turns a stream of HID reports into button presses without knowing the device.
    A button = a byte that goes from 0 to a value and straight back to 0 (press, then
    release). Works for both bit-per-button and button-number style reports; movement
    and analog data (bytes that wander through many values) never qualify."""

    def __init__(self):
        self.prev, self.values, self.ids, self.pending = {}, {}, {}, {}

    MIN_HOLD = 0.04      # a person holds a button at least ~40 ms; movement data changes faster

    def feed(self, rep, now=None):
        now = time.time() if now is None else now
        key = (len(rep), rep[0] if rep else 0)
        prev = self.prev.get(key)
        self.prev[key] = rep
        for i, b in enumerate(rep):
            seen = self.values.setdefault((key, i), set())
            if len(seen) <= 32:
                seen.add(b)
        presses = []
        if prev is None:
            return presses
        changed = [i for i in range(len(rep)) if rep[i] != prev[i]]
        if len(changed) > 3:                       # lots changing at once: movement
            for i in changed:
                self.pending.pop((key, i), None)
            return presses
        for i in changed:
            old, new, slot = prev[i], rep[i], (key, i)
            if len(self.values[slot]) > 12:          # analog-looking byte
                self.pending.pop(slot, None)
                continue
            if old == 0 and new:                     # pressed - wait for the release
                self.pending[slot] = (new, now)
            elif new == 0 and slot in self.pending and self.pending[slot][0] == old:
                _v, t0 = self.pending.pop(slot)      # released
                if now - t0 < self.MIN_HOLD:
                    continue                         # too quick for a finger
                if len(self.values[slot]) > 12:
                    continue
                bid = (key, i, old)
                if bid not in self.ids:
                    self.ids[bid] = len(self.ids) + 1
                presses.append(self.ids[bid])
            else:
                self.pending.pop(slot, None)         # wandered to another value: not a button
        return presses


class HidrawWatcher:
    """Calls on_press(button_number) for buttons seen in raw HID reports.
    on_state gets 'listening', 'noperm' or 'none'."""

    def __init__(self, nodes, on_press, on_state):
        self.nodes, self.on_press, self.on_state = nodes, on_press, on_state
        self.stop_flag = False
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag = True

    def _run(self):
        fds, noperm = {}, False
        for n in self.nodes:
            try:
                fds[os.open(n, os.O_RDONLY | os.O_NONBLOCK)] = RawButtons()
            except PermissionError:
                noperm = True
            except OSError:
                pass
        if not fds:
            self.on_state("noperm" if noperm else "none")
            return
        self.on_state("listening")
        try:
            while not self.stop_flag:
                r, _w, _x = select.select(list(fds), [], [], 0.2)
                for fd in r:
                    try:
                        rep = os.read(fd, 256)
                    except BlockingIOError:
                        continue
                    except OSError:
                        return
                    for b in fds[fd].feed(rep):
                        self.on_press(b)
        finally:
            for fd in fds:
                os.close(fd)
