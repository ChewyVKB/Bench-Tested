"""Shared helpers: commands, settings, USB detection, Code 128, CUPS."""

import datetime  # noqa: F401  (re-exported for modules)
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import traceback  # noqa: F401
import urllib.parse

APP_TITLE = "Bench Tested"
APP_ID = "bench-tested"
CONFIG_PATH = os.path.expanduser("~/.config/bench-tested.json")
OLD_CONFIG_PATH = os.path.expanduser("~/.config/label-printer-test.json")
DATA_DIR = os.path.expanduser("~/.local/share/bench-tested")
CACHE_DIR = os.path.expanduser("~/.cache")
ENV_C = dict(os.environ, LC_ALL="C", LANG="C")

# ------------------------------------------------------------------ categories
# Order here = order in the device list.
CATEGORIES = [
    ("printer", "Label Printers"),
    ("scanner", "Barcode Scanners"),
    ("mic", "Dictation Mics"),
    ("docscan", "Document Scanners"),
    ("sigpad", "Signature Pads"),
    ("camera", "Cameras & Conference"),
]
CATEGORY_TITLES = dict(CATEGORIES)

PRINTER_VENDORS = {"0a5f": "Zebra", "0922": "Dymo"}
# dictation mics: vendor -> name (product name checked too)
MIC_VENDORS = {"0554": "Nuance", "0911": "Philips"}
MIC_NAMES = r"powermic|speechmike|speechone|dictation|recmic|foot ?control"
# document scanners: vendor -> (name, product pattern)
DOCSCAN_VENDORS = {
    "04c5": ("Fujitsu", r"fi-|scansnap|scanner"),
    "05ca": ("Ricoh / PFU", r"fi-|scansnap|scanner"),
    "1083": ("Canon", r"DR-|scanner"),
    "04f9": ("Brother", r"ADS-"),
    "04b8": ("Epson", r"\bDS-|scanner"),
    "040a": ("Kodak Alaris", r"scanner|\bi\d{3,4}\b|\bS\d{4}"),
    "0638": ("Avision", r"scanner|\bAD\d"),
}
SKIP_VENDORS = {"df04"}   # PowerMic trackball - shown as part of the mic

SCANNER_VENDORS = {
    "05e0": "Symbol / Zebra",
    "0c2e": "Honeywell",
    "0536": "Honeywell (Hand Held Products)",
    "05f9": "Datalogic",
    "065a": "Opticon",
    "1eab": "Newland",
}


def run(cmd, timeout=60, **kw):
    """Run a command, never raise; return CompletedProcess-like object."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    if cmd and cmd[0] != "pkexec":
        # no terminal: a command that wants a password fails right away instead of hanging
        kw.setdefault("start_new_session", True)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=ENV_C, **kw)
    except Exception as e:  # missing program, timeout...
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def all_usb_ids():
    """Serials and product names of everything plugged in over USB (any brand)."""
    serials, products = set(), set()
    for d in glob.glob("/sys/bus/usb/devices/*"):
        if ":" in os.path.basename(d):
            continue
        s = read_file(d + "/serial")
        if s:
            serials.add(s)
        p = read_file(d + "/product")
        if p:
            products.add(norm(p))
    return serials, products


def norm(s):
    return " ".join(s.lower().replace("dymo", "").replace("zebra", "").split())


C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
        "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
        "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
        "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
        "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
        "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
        "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
        "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
        "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
        "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
        "114131 311141 411131 211412 211214 211232").split()


C128_STOP = "2331112"


def code128_modules(data):
    """Code 128 (set B) as a list of (is_bar, width_in_modules)."""
    vals = [104] + [ord(c) - 32 for c in data]
    check = (104 + sum(i * v for i, v in enumerate(vals[1:], 1))) % 103
    pats = [C128[v] for v in vals + [check]] + [C128_STOP]
    out = []
    for p in pats:
        for i, ch in enumerate(p):
            out.append((i % 2 == 0, int(ch)))
    return out


def code128_width(data):
    return 11 * (len(data) + 2) + 13


def ps_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def cups_queues():
    """{queue_name: device_uri}"""
    q = {}
    for line in run(["lpstat", "-v"]).stdout.splitlines():
        m = re.match(r"^device for (\S+):\s*(\S+)", line)
        if m:
            q[m.group(1)] = m.group(2)
    return q


def uri_serial(uri):
    qs = urllib.parse.urlparse(uri).query
    return urllib.parse.parse_qs(qs).get("serial", [""])[0]


def admin_run(cmds, log):
    """Run printer-admin commands; if not allowed as this user, ask for the password once."""
    failed_at = None
    for i, c in enumerate(cmds):
        r = run(c)
        if r.returncode != 0:
            failed_at = i
            break
    if failed_at is None:
        return True
    rest = cmds[failed_at:]
    log("Administrator permission needed - a password prompt will appear.")
    script = " && ".join(shlex.join(c) for c in rest)
    r = run(["pkexec", "sh", "-c", script], timeout=600)
    if r.returncode != 0:
        log("Couldn't make the change: " + (r.stderr.strip() or "permission denied"))
        return False
    return True


def load_config():
    for path in (CONFIG_PATH, OLD_CONFIG_PATH):   # settings from Label Printer Tester carry over
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


# ------------------------------------------------------------------ USB detection

def usb_interfaces(sysdir):
    """[(class, subclass, protocol)] of a USB device's interfaces, as hex strings."""
    base = os.path.basename(sysdir)
    out = []
    for i in sorted(glob.glob(os.path.join(sysdir, base + ":*"))):
        out.append((read_file(i + "/bInterfaceClass").lower(),
                    read_file(i + "/bInterfaceSubClass").lower(),
                    read_file(i + "/bInterfaceProtocol").lower()))
    return out


def classify(vid, product, ifaces, serial="", removable=""):
    """Which category a USB device belongs to (or None)."""
    classes = {c for c, _s, _p in ifaces}
    if vid == "06a8" or re.search(r"topaz", f"{product} {serial}", re.I):
        return "sigpad"                   # Topaz: own USB id (HID models) or FTDI serial chip "TOPAZ..."
    if vid in SKIP_VENDORS:
        return None
    if vid in DOCSCAN_VENDORS and (re.search(DOCSCAN_VENDORS[vid][1], product, re.I) or
                                   (vid in ("04c5", "05ca") and "ff" in classes and
                                    not re.search(r"finger|keyboard|mouse|reader", product, re.I))):
        return "docscan"
    if (vid in MIC_VENDORS or re.search(MIC_NAMES, product, re.I)) and "01" in classes:
        return "mic"
    if vid in MIC_VENDORS and re.search(MIC_NAMES, product, re.I):
        return "mic"
    if "0e" in classes:                  # USB video: webcams, conference cameras
        if removable == "fixed" or re.search(r"integrated|built-?in", product, re.I):
            return None                   # the laptop's own camera
        return "camera"
    if vid in PRINTER_VENDORS and ("07" in classes or re.search(r"label", product, re.I)):
        return "printer"
    if "07" in classes:
        return None                       # some other printer
    if vid in SCANNER_VENDORS:
        return "scanner"
    if re.search(r"barcode|bar code|scanner|imager", product, re.I) and \
            ("03" in classes or "02" in classes or "0a" in classes):
        return "scanner"
    return None


_DB_NAMES = {}


def db_names(sysdir):
    """Vendor/model names from Ubuntu's device database, for devices that don't
    report their own name over USB (some scanners)."""
    if sysdir not in _DB_NAMES:
        props = {}
        r = run(["udevadm", "info", "-q", "property", "-p", sysdir], timeout=5)
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            props[k] = v
        _DB_NAMES[sysdir] = (props.get("ID_VENDOR_FROM_DATABASE", ""), props.get("ID_MODEL_FROM_DATABASE", ""))
    return _DB_NAMES[sysdir]


def scan_usb():
    """Supported devices that are plugged in, read straight from sysfs."""
    devs = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        if ":" in os.path.basename(d):
            continue
        vid = read_file(d + "/idVendor").lower()
        if not vid:
            continue
        product = read_file(d + "/product")
        if not product and (vid in DOCSCAN_VENDORS or vid in MIC_VENDORS or vid in SCANNER_VENDORS
                            or vid in PRINTER_VENDORS):
            product = db_names(os.path.realpath(d))[1]
        ifaces = usb_interfaces(d)
        serial = read_file(d + "/serial")
        cat = classify(vid, product, ifaces, serial, read_file(d + "/removable"))
        if not cat:
            continue
        if cat == "printer":
            brand = PRINTER_VENDORS[vid]
        elif cat == "docscan":
            brand = DOCSCAN_VENDORS[vid][0]
        elif cat == "sigpad":
            brand = "Topaz"
            if not re.search(r"topaz", product, re.I):
                product = "Topaz signature pad" + (" (serial)" if vid == "0403" else "")
        elif cat == "camera":
            brand = read_file(d + "/manufacturer") or db_names(os.path.realpath(d))[0] or "Camera"
            brand = re.sub(r",? ?(Inc|Ltd|Corp|Co)\.?.*$", "", brand).strip() or "Camera"
            product = product or db_names(os.path.realpath(d))[1] or "USB camera"
        elif cat == "mic":
            brand = MIC_VENDORS.get(vid) or read_file(d + "/manufacturer") or "Mic"
        else:
            brand = SCANNER_VENDORS.get(vid) or read_file(d + "/manufacturer") or "Scanner"
        devs.append({
            "category": cat,
            "brand": brand,
            "vid": vid,
            "pid": read_file(d + "/idProduct").lower(),
            "product": product or f"{brand} device",
            "manufacturer": read_file(d + "/manufacturer"),
            "serial": serial,
            "sysdir": os.path.realpath(d),
            "ifaces": ifaces,
            "lp": None,
        })
    for lp in glob.glob("/sys/class/usbmisc/lp*"):
        parent = os.path.dirname(os.path.realpath(lp + "/device"))
        for dv in devs:
            if dv["sysdir"] == parent:
                dv["lp"] = "/dev/usb/" + os.path.basename(lp)
    for dv in devs:
        dv["key"] = dv["serial"] or dv["sysdir"]
        # changes every time the device is plugged in (USB gives it a new number), so a
        # different unit in the same port - or the same unit replugged - starts fresh
        dv["plug"] = f"{dv['sysdir']}@{read_file(dv['sysdir'] + '/devnum')}"
    order = {c: i for i, (c, _t) in enumerate(CATEGORIES)}
    devs.sort(key=lambda d: (order.get(d["category"], 99), d["brand"], d["product"], d["key"]))
    return devs


def display_name(dv):
    p = dv["product"]
    first = dv["brand"].split()[0].lower()
    if first in p.lower():
        return p
    return f"{dv['brand'].split(' (')[0]} {p}"


def short_name(d):
    p = d["product"].replace("DYMO", "").replace("ZTC", "").strip()
    return f"{p} …{d['serial'][-4:]}" if d["serial"] else p
