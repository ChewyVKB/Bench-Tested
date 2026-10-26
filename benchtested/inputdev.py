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
