"""GTK import and small widget helpers shared by every page."""

import threading

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: F401
except Exception:  # pragma: no cover - only when GTK isn't installed
    Gtk = GLib = Pango = GdkPixbuf = None


def call_main(fn, *args):
    """Run fn on the GUI thread and wait for its result (for dialogs from workers)."""
    ev, res = threading.Event(), {}

    def _do():
        try:
            res["v"] = fn(*args)
        finally:
            ev.set()
        return False
    GLib.idle_add(_do)
    ev.wait()
    return res.get("v")


def combo(items, active=None):
    c = Gtk.ComboBoxText()
    for cid, text in items:
        c.append(cid, text)
    c.set_active_id(active or items[0][0])
    return c


def lbl(text="", xalign=0.0, markup=False, selectable=False, wrap=False):
    w = Gtk.Label()
    if markup:
        w.set_markup(text)
    else:
        w.set_text(text)
    w.set_xalign(xalign)
    w.set_selectable(selectable)
    if wrap:
        w.set_line_wrap(True)
        w.set_max_width_chars(60)
    return w


def dim(widget):
    widget.get_style_context().add_class("dim-label")
    return widget
