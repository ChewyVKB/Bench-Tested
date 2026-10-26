"""Main window: every supported device that's plugged in, grouped by type, with a
page per device type and a shared Activity log."""

from .common import *  # noqa: F401,F403
from .gtkutil import GLib, Gtk, dim, lbl
from .printers import PrinterMixin
from .barcode import BarcodeMixin
from .mic import MicMixin
from .docscan import DocScanMixin


class App(PrinterMixin, BarcodeMixin, MicMixin, DocScanMixin, Gtk.Window if Gtk else object):
    def __init__(self):
        super().__init__(title=APP_TITLE)
        self.set_default_size(1040, 760)
        self.set_icon_name("applications-engineering")
        self.cfg = load_config()
        self.devices = []
        self.selected_key = None
        self.dev_sig = None
        self.busy_keys = set()       # devices that are working right now
        self.global_busy = False     # cleanup / batch preparation
        self.checked = set()         # printers ticked for "Print checked"
        self.row_status = {}         # key -> status text shown under the device
        self.row_ui = {}             # key -> (check or None, spinner, status label)
        self.settings = {}           # key -> per-printer options
        self.page_widgets = []
        self.admin_lock = threading.Lock()
        self._tl = threading.local()
        self._loading = False
        self.barcode_init()
        self.mic_init()
        self.docscan_init()

        hb = Gtk.HeaderBar(title=APP_TITLE, subtitle="Plug in a device to test it")
        hb.set_show_close_button(True)
        self.set_titlebar(hb)
        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)
        b_refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        b_refresh.set_tooltip_text("Look for devices again now")
        b_refresh.connect("clicked", lambda *_: self.manual_refresh())
        hb.pack_end(b_refresh)
        self.b_clean = Gtk.Button(label="Clean up printers…")
        self.b_clean.set_tooltip_text("Remove printers that aren't connected any more")
        self.b_clean.connect("clicked", lambda *_: self.cleanup_dialog())
        hb.pack_start(self.b_clean)
        b_sys = Gtk.Button(label="System printer settings")
        b_sys.connect("clicked", lambda *_: self.open_system_printers())
        hb.pack_start(b_sys)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        outer.pack_start(paned, True, True, 0)

        # ---- left: everything plugged in, grouped by type
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_border_width(10)
        left.pack_start(lbl("<b>Connected devices</b>", markup=True), False, False, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_header_func(self.row_header)
        self.listbox.connect("row-selected", self.on_row_selected)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.listbox)
        sw.set_size_request(330, -1)
        left.pack_start(sw, True, True, 0)
        self.empty_hint = dim(lbl("Nothing plugged in yet.\nPlug in a label printer, barcode scanner, "
                                  "dictation mic or document scanner - it shows up here by itself.", wrap=True))
        left.pack_start(self.empty_hint, False, False, 0)
        self.batch_box = Gtk.Box(spacing=6)
        b_all = Gtk.Button(label="Tick all printers")
        b_all.set_tooltip_text("Tick every label printer (click again to untick)")
        b_all.connect("clicked", lambda *_: self.toggle_all())
        self.batch_box.pack_start(b_all, False, False, 0)
        self.b_batch = Gtk.Button(label="Print checked (0)")
        self.b_batch.get_style_context().add_class("suggested-action")
        self.b_batch.set_tooltip_text("Print a test label on every ticked printer at the same time, "
                                      "each with its own settings")
        self.b_batch.connect("clicked", lambda *_: self.print_checked())
        self.batch_box.pack_start(self.b_batch, True, True, 0)
        left.pack_start(self.batch_box, False, False, 0)
        b_ref = Gtk.Button()
        b_ref_box = Gtk.Box(spacing=6)
        b_ref_box.set_halign(Gtk.Align.CENTER)
        b_ref_box.pack_start(Gtk.Image.new_from_icon_name("view-refresh-symbolic",
                                                          Gtk.IconSize.BUTTON), False, False, 0)
        b_ref_box.pack_start(Gtk.Label(label="Refresh devices"), False, False, 0)
        b_ref.add(b_ref_box)
        b_ref.set_tooltip_text("Unplugged one device and plugged in another? Click to update now.")
        b_ref.connect("clicked", lambda *_: self.manual_refresh())
        left.pack_start(b_ref, False, False, 0)
        paned.pack1(left, False, False)

        # ---- right: a page per device type
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        none_page = dim(lbl("Select a device on the left.", xalign=0.5))
        self.stack.add_named(none_page, "none")
        self.stack.add_named(self.build_zebra_page(), "zebra")
        self.stack.add_named(self.build_dymo_page(), "dymo")
        self.stack.add_named(self.build_barcode_page(), "scanner")
        self.stack.add_named(self.build_mic_page(), "mic")
        self.stack.add_named(self.build_docscan_page(), "docscan")
        paned.pack2(self.stack, True, False)

        # ---- bottom: activity log
        frame = Gtk.Frame(label=" Activity ")
        frame.set_border_width(8)
        self.logview = Gtk.TextView()
        self.logview.set_editable(False)
        self.logview.set_cursor_visible(False)
        self.logview.set_monospace(True)
        self.logview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.logscroll = Gtk.ScrolledWindow()
        self.logscroll.set_size_request(-1, 150)
        self.logscroll.add(self.logview)
        frame.add(self.logscroll)
        outer.pack_start(frame, False, True, 0)

        self.connect("destroy", self.on_quit)
        self.show_all()
        self.empty_hint.hide()
        self.refresh_devices()
        GLib.timeout_add_seconds(1, self.refresh_devices)
        self.update_sensitivity()
        self.log("Ready. Plug in a device and pick it on the left.")

    def on_quit(self, *_a):
        for lst in self.listeners.values():
            lst.stop()
        for st in self.mic_state.values():
            for w in st["watchers"]:
                w.stop()
        for st in self.doc_state.values():
            if st["proc"] and st["proc"].poll() is None:
                st["proc"].terminate()
        Gtk.main_quit()

    def log(self, text):
        prefix = getattr(self._tl, "prefix", "")

        def _do():
            buf = self.logview.get_buffer()
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            buf.insert(buf.get_end_iter(), f"{stamp}  {prefix}{text}\n")
            GLib.idle_add(self._scroll_log)
            return False
        GLib.idle_add(_do)

    def _scroll_log(self):
        adj = self.logscroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def in_thread(self, fn, *args, prefix=""):
        """Background thread that logs with a printer-name prefix."""
        def _run():
            self._tl.prefix = prefix
            try:
                fn(*args)
            except Exception:
                self.log("Something went wrong:\n" + traceback.format_exc())
        threading.Thread(target=_run, daemon=True).start()

    def set_row_status(self, key, text, busy=None):
        def _do():
            self.row_status[key] = text
            ui = self.row_ui.get(key)
            if ui:
                check, spin, status = ui
                status.set_text(text)
                status.set_visible(bool(text))
                if busy is not None:
                    (spin.start if busy else spin.stop)()
                    spin.set_visible(busy)
            return False
        GLib.idle_add(_do)

    def task(self, d, fn, *args):
        """Run fn(*args) for one printer in the background. Only that printer is
        locked while it works - everything else stays usable."""
        if not d or d["key"] in self.busy_keys:
            return False
        key = d["key"]
        self.busy_keys.add(key)
        self.set_row_status(key, "Working…", busy=True)
        self.update_sensitivity()

        def _work():
            self._tl.prefix = f"[{short_name(d)}] "
            ok = None
            try:
                ok = fn(*args)
            except Exception:
                self.log("Something went wrong:\n" + traceback.format_exc())
            stamp = datetime.datetime.now().strftime("%H:%M")
            if isinstance(ok, str):          # the test wrote its own summary
                text = ok
            else:
                text = f"✓ Done {stamp}" if ok else "✗ Didn't finish - see Activity"
            self.set_row_status(key, text, busy=False)

            def _done():
                self.busy_keys.discard(key)
                self.update_sensitivity()
                return False
            GLib.idle_add(_done)
        threading.Thread(target=_work, daemon=True).start()
        return True

    def global_task(self, fn, *args):
        if self.global_busy:
            return
        self.global_busy = True
        self.update_sensitivity()

        def _work():
            self._tl.prefix = ""
            try:
                fn(*args)
            except Exception:
                self.log("Something went wrong:\n" + traceback.format_exc())

            def _done():
                self.global_busy = False
                self.update_sensitivity()
                return False
            GLib.idle_add(_done)
        threading.Thread(target=_work, daemon=True).start()

    def message(self, text, secondary="", kind=None):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=kind or Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text=text)
        if secondary:
            d.format_secondary_text(secondary)
        d.run()
        d.destroy()

    def open_system_printers(self):
        for cmd in (["gnome-control-center", "printers"], ["system-config-printer"],
                    ["xdg-open", "http://localhost:631/printers/"]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

    def current(self):
        return self.device(self.selected_key)

    def device(self, key):
        for d in self.devices:
            if d["key"] == key:
                return d
        return None

    def find_again(self, key, brand):
        """Find the same printer after it re-appears (e.g. new /dev/usb/lp number)."""
        for d in scan_usb():
            if d["key"] == key and d["brand"] == brand:
                return d
        return None

    def update_sensitivity(self):
        cur_busy = self.selected_key in self.busy_keys
        for w in self.page_widgets:
            w.set_sensitive(not cur_busy)
        self.b_clean.set_sensitive(not self.busy_keys and not self.global_busy)
        printers = [d for d in self.devices if d["category"] == "printer"]
        n = len([d for d in printers if d["key"] in self.checked])
        self.b_batch.set_label(f"Print checked ({n})")
        self.b_batch.set_sensitive(n > 0 and not self.global_busy)
        self.batch_box.set_visible(len(printers) > 0)
        if self.busy_keys or self.global_busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def manual_refresh(self):
        self.dev_sig = None          # force the list to rebuild
        self.refresh_devices()
        row = self.listbox.get_selected_row()
        if row is not None:
            self.on_row_selected(self.listbox, row)
        n = len(self.devices)
        self.log(f"Refreshed - {n} device{'s' if n != 1 else ''} connected." if n
                 else "Refreshed - no supported device found. Check the cable and power.")

    # ============================================================ device list
    def refresh_devices(self):
        devs = scan_usb()
        sig = [(d["key"], d["lp"], d["category"]) for d in devs]
        if sig != self.dev_sig:
            first_time = self.dev_sig is None
            self.dev_sig = sig
            self.devices = devs
            self.rebuild_rows()
            self.barcode_devices_changed(devs)
            self.mic_devices_changed(devs)
            self.docscan_devices_changed(devs)
            if not first_time:
                self.in_thread(self.forget_unplugged_units)
        return True

    def row_header(self, row, before):
        cat = getattr(row, "category", None)
        if before is None or getattr(before, "category", None) != cat:
            h = lbl(f"<small><b>{CATEGORY_TITLES.get(cat, '').upper()}</b></small>", markup=True)
            h.set_margin_top(10 if before is not None else 2)
            h.set_margin_bottom(2)
            h.set_margin_start(6)
            dim(h)
            row.set_header(h)
        else:
            row.set_header(None)

    def rebuild_rows(self):
        devs = self.devices
        self.listbox.handler_block_by_func(self.on_row_selected)
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        self.row_ui = {}
        select_row = None
        for d in devs:
            key = d["key"]
            row = Gtk.ListBoxRow()
            row.key = key
            row.category = d["category"]
            box = Gtk.Box(spacing=8)
            box.set_border_width(6)
            check = None
            if d["category"] == "printer":
                check = Gtk.CheckButton()
                check.set_active(key in self.checked)
                check.set_tooltip_text("Include in 'Print checked'")
                check.connect("toggled", self.on_check, key)
                box.pack_start(check, False, False, 0)
            icon = {"printer": "printer", "scanner": "scanner", "mic": "audio-input-microphone",
                    "docscan": "scanner"}.get(d["category"], "drive-removable-media")
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DND), False, False, 0)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            texts.pack_start(lbl(f"<b>{GLib.markup_escape_text(display_name(d))}</b>",
                                 markup=True), False, False, 0)
            sub = (f"SN {d['serial']}" if d["serial"] else "no serial") + \
                  (f"  ·  {d['lp']}" if d["brand"] == "Zebra" and d["lp"] else "")
            texts.pack_start(dim(lbl(sub)), False, False, 0)
            status = lbl(self.row_status.get(key, ""))
            texts.pack_start(status, False, False, 0)
            box.pack_start(texts, True, True, 0)
            spin = Gtk.Spinner()
            box.pack_end(spin, False, False, 0)
            row.add(box)
            self.listbox.add(row)
            self.row_ui[key] = (check, spin, status)
            if key == self.selected_key:
                select_row = row
        self.listbox.show_all()
        for key, (_check, spin, status) in self.row_ui.items():
            busy = key in self.busy_keys
            spin.set_visible(busy)
            if busy:
                spin.start()
            status.set_visible(bool(self.row_status.get(key)))
        self.empty_hint.set_visible(not devs)
        self.listbox.handler_unblock_by_func(self.on_row_selected)
        if select_row is None and devs:
            select_row = self.listbox.get_row_at_index(0)
        if select_row is not None:
            if select_row.key == self.selected_key:
                # same device (maybe a new /dev number) - keep its page as-is
                self.listbox.handler_block_by_func(self.on_row_selected)
                self.listbox.select_row(select_row)
                self.listbox.handler_unblock_by_func(self.on_row_selected)
                self.update_header()
            else:
                self.listbox.select_row(select_row)
        else:
            self.selected_key = None
            self.stack.set_visible_child_name("none")
        self.update_sensitivity()

    def on_check(self, button, key):
        if button.get_active():
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.update_sensitivity()

    def toggle_all(self):
        keys = {d["key"] for d in self.devices if d["category"] == "printer"}
        all_on = keys and keys <= self.checked
        for _key, (check, _s, _st) in self.row_ui.items():
            if check is not None:
                check.set_active(not all_on)

    def on_row_selected(self, _lb, row):
        if row is None:
            return
        self.selected_key = row.key
        d = self.current()
        if not d:
            return
        if d["category"] == "printer":
            self.stack.set_visible_child_name("zebra" if d["brand"] == "Zebra" else "dymo")
            self.update_header()
            if d["brand"] == "Zebra":
                self.load_zebra_opts(d)
            else:
                self.d_refresh_status(d)
        elif d["category"] == "scanner":
            self.stack.set_visible_child_name("scanner")
            self.barcode_select(d)
        elif d["category"] == "mic":
            self.stack.set_visible_child_name("mic")
            self.mic_select(d)
        elif d["category"] == "docscan":
            self.stack.set_visible_child_name("docscan")
            self.docscan_select(d)
        self.update_sensitivity()

    def update_header(self):
        d = self.current()
        if not d or d["category"] != "printer":
            return
        title = f"<big><b>{GLib.markup_escape_text(display_name(d))}</b></big>"
        if d["brand"] == "Zebra":
            self.z_title.set_markup(title)
            self.z_sub.set_text(f"Serial {d['serial'] or '?'}  ·  "
                                f"{d['lp'] or 'not available as /dev/usb/lp*'}")
        else:
            self.d_title.set_markup(title)
            self.d_sub.set_text(f"Serial {d['serial'] or '?'}")


def main():
    if Gtk is None:
        msg = ("Bench Tested needs GTK for Python. Install it with:\n\n"
               "    sudo apt install python3-gi gir1.2-gtk-3.0")
        if shutil.which("zenity"):
            subprocess.run(["zenity", "--error", "--width=400", "--text", msg])
        print(msg, file=sys.stderr)
        sys.exit(1)
    App()
    Gtk.main()
