"""Device test certificates as PDF (ReportLab). No GTK in here, so it can be tested on its own.

build_pdf(path, data) - data is a dict:
    company, logo (path or None), cert_id, result ('PASS' | 'FAIL' | 'INCOMPLETE'),
    result_note, asset_tag, device [(label, value)], details [(label, value)],
    checks [(name, status, detail)] with status 'pass' | 'fail' | 'info' | 'skip',
    signature {strokes, bounds, flip} or None, images [(caption, path)], files [path],
    activity [(datetime, text)], tech, started, completed, station, software, notes
"""

import datetime
import os

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

# Colours follow the company logo (S3 Recycling Solutions green and grey). Red and amber are
# kept only where they carry meaning: a failed or incomplete result.
GREEN = colors.HexColor("#46a944")
GREY = colors.HexColor("#6d6e70")
INK = colors.HexColor("#2b2d2f")
MUTED = colors.HexColor("#6d6e70")
LINE = colors.HexColor("#dfe3df")
BAND = colors.HexColor("#f3f8f2")
ACCENT = GREEN
RESULT_COLORS = {"PASS": GREEN, "FAIL": colors.HexColor("#c0392b"), "INCOMPLETE": colors.HexColor("#c98a1b")}
STATUS = {"pass": ("PASS", GREEN), "fail": ("FAIL", colors.HexColor("#c0392b")),
          "info": ("INFO", GREY), "skip": ("NOT TESTED", colors.HexColor("#a3a6a8"))}

_FONTS = None


def fonts():
    """(regular, bold, mono) - DejaVu when installed (it has every symbol), else built-ins."""
    global _FONTS
    if _FONTS:
        return _FONTS
    base = "/usr/share/fonts/truetype/dejavu/"
    try:
        pdfmetrics.registerFont(TTFont("BT-Sans", base + "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("BT-Bold", base + "DejaVuSans-Bold.ttf"))
        pdfmetrics.registerFont(TTFont("BT-Mono", base + "DejaVuSansMono.ttf"))
        _FONTS = ("BT-Sans", "BT-Bold", "BT-Mono")
    except Exception:
        _FONTS = ("Helvetica", "Helvetica-Bold", "Courier")
    return _FONTS


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))


def fmt_time(t):
    return t.strftime("%b %d, %Y  %I:%M:%S %p") if isinstance(t, datetime.datetime) else str(t or "-")


class Pill(Flowable):
    """A rounded status label."""

    def __init__(self, text, color, w=0.95 * inch, h=0.2 * inch, size=7.5):
        super().__init__()
        self.text, self.color, self.width, self.height, self.size = text, color, w, h, size

    def draw(self):
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 0, self.width, self.height, self.height / 2, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(fonts()[1], self.size)
        c.drawCentredString(self.width / 2, self.height / 2 - self.size * 0.35, self.text)


class Signature(Flowable):
    """The signature drawn on the pad, as vector strokes inside a light frame."""

    def __init__(self, sig, w=6.9 * inch, h=1.7 * inch):
        super().__init__()
        self.sig, self.width, self.height = sig, w, h

    def draw(self):
        c = self.canv
        c.setStrokeColor(LINE)
        c.setFillColor(colors.white)
        c.roundRect(0, 0, self.width, self.height, 6, stroke=1, fill=1)
        x0, y0, x1, y1 = self.sig["bounds"]
        pad = 10
        sx = (self.width - 2 * pad) / max(1, x1 - x0)
        sy = (self.height - 2 * pad) / max(1, y1 - y0)
        s = min(sx, sy)
        ox = (self.width - (x1 - x0) * s) / 2
        oy = (self.height - (y1 - y0) * s) / 2
        flip = self.sig.get("flip")

        def f(x, y):
            px, py = (x - x0) * s + ox, (y - y0) * s + oy      # pad y grows downwards
            return (self.width - px, py) if flip else (px, self.height - py)
        c.setStrokeColor(colors.HexColor("#0d2690"))
        c.setLineWidth(1.4)
        c.setLineCap(1)
        c.setLineJoin(1)
        for stroke in self.sig["strokes"]:
            if len(stroke) < 2:
                if stroke:
                    px, py = f(*stroke[0])
                    c.circle(px, py, 0.7, stroke=0, fill=1)
                continue
            p = c.beginPath()
            p.moveTo(*f(*stroke[0]))
            for pt in stroke[1:]:
                p.lineTo(*f(*pt))
            c.drawPath(p, stroke=1, fill=0)


def scaled_image(path, max_w, max_h):
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    s = min(max_w / w, max_h / h, 1.0 if w > max_w else max_w / w)
    return Image(path, width=w * s, height=h * s)


def build_pdf(path, data):
    regular, bold, mono = fonts()
    st = {
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=10.5, textColor=ACCENT, spaceBefore=12,
                             spaceAfter=6, leading=13),
        "body": ParagraphStyle("body", fontName=regular, fontSize=8.8, textColor=INK, leading=11.5),
        "label": ParagraphStyle("label", fontName=regular, fontSize=7.6, textColor=MUTED, leading=10),
        "value": ParagraphStyle("value", fontName=bold, fontSize=9, textColor=INK, leading=11.5),
        "small": ParagraphStyle("small", fontName=regular, fontSize=7.8, textColor=MUTED, leading=10),
        "mono": ParagraphStyle("mono", fontName=mono, fontSize=7.4, textColor=INK, leading=9.6),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.4, textColor=INK, leading=10.8),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=8.4, textColor=INK, leading=10.8),
        "right": ParagraphStyle("right", fontName=regular, fontSize=8, textColor=MUTED, leading=10,
                                alignment=TA_RIGHT),
    }
    W = letter[0] - 1.2 * inch                     # usable width
    result = data.get("result", "INCOMPLETE")
    rcolor = RESULT_COLORS.get(result, MUTED)
    story = []

    # ---- summary band: asset tag (with barcode), result, certificate number
    tag = data.get("asset_tag") or ""
    tag_cell = [Paragraph("ASSET TAG", st["label"]),
                Paragraph(esc(tag) if tag else "<font color='#c0392b'>Not entered</font>",
                          ParagraphStyle("tag", fontName=bold, fontSize=15, textColor=INK, leading=19))]
    if tag:
        try:
            bc = code128.Code128(tag, barHeight=0.36 * inch, barWidth=0.011 * inch, quiet=False)
            tag_cell.append(Spacer(1, 3))
            tag_cell.append(bc)
        except Exception:
            pass
    res_cell = [Paragraph("RESULT", st["label"]), Spacer(1, 2),
                Pill(result, rcolor, w=1.45 * inch, h=0.34 * inch, size=13)]
    if data.get("result_note"):
        res_cell += [Spacer(1, 3), Paragraph(esc(data["result_note"]), st["small"])]
    id_cell = [Paragraph("CERTIFICATE NO.", st["label"]),
               Paragraph(esc(data.get("cert_id", "")), st["value"]), Spacer(1, 5),
               Paragraph("COMPLETED", st["label"]),
               Paragraph(esc(fmt_time(data.get("completed"))), st["value"])]
    band = Table([[tag_cell, res_cell, id_cell]], colWidths=[W * 0.42, W * 0.25, W * 0.33])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBEFORE", (1, 0), (2, 0), 0.6, LINE)]))
    story += [band]

    def kv_table(pairs, cols=2):
        cells, row = [], []
        for label, value in pairs:
            row.append([Paragraph(esc(label).upper(), st["label"]),
                        Paragraph(esc(value if value not in (None, "") else "Not reported"), st["value"])])
            if len(row) == cols:
                cells.append(row)
                row = []
        if row:
            cells.append(row + [""] * (cols - len(row)))
        t = Table(cells, colWidths=[W / cols] * cols)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                               ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE)]))
        return t

    # ---- device
    story += [Paragraph("Device", st["h2"]), kv_table(data.get("device", []))]
    if data.get("details"):
        story += [Paragraph("Device details", st["h2"]), kv_table(data["details"])]

    # ---- test results
    checks = data.get("checks", [])
    rows = [[Paragraph("TEST", st["label"]), Paragraph("RESULT", st["label"]),
             Paragraph("DETAILS", st["label"])]]
    for name, status, detail in checks:
        text, col = STATUS.get(status, STATUS["info"])
        rows.append([Paragraph(esc(name), st["cellb"]), Pill(text, col), Paragraph(esc(detail or ""), st["cell"])])
    if len(rows) == 1:
        rows.append([Paragraph("No tests were run on this device.", st["cell"]), "", ""])
    t = Table(rows, colWidths=[W * 0.32, W * 0.16, W * 0.52], repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")])]))
    passed = sum(1 for c in checks if c[1] == "pass")
    failed = sum(1 for c in checks if c[1] == "fail")
    story += [Paragraph(f"Test results <font color='#6d6e70' size='8'>&nbsp;&nbsp;{passed} passed"
                        f" · {failed} failed · {len(checks)} total</font>", st["h2"]), t]

    # ---- evidence
    ev = []
    if data.get("signature") and data["signature"].get("strokes"):
        ev += [KeepTogether([Paragraph("Evidence", st["h2"]),
                             Paragraph("Signature captured from the pad", st["small"]), Spacer(1, 3),
                             Signature(data["signature"], w=W, h=1.7 * inch), Spacer(1, 8)])]
    imgs = [(c, p) for c, p in data.get("images", []) if p and os.path.exists(p)]
    if imgs:
        n = min(3, len(imgs))
        cw = W / n
        cells = []
        for cap, p in imgs[:6]:
            im = scaled_image(p, cw - 12, 2.2 * inch)
            cells.append([im or Paragraph("(image unavailable)", st["small"]), Spacer(1, 3),
                          Paragraph(esc(cap), st["small"])])
        grid = [cells[i:i + n] for i in range(0, len(cells), n)]
        grid[-1] += [""] * (n - len(grid[-1]))
        g = Table(grid, colWidths=[cw] * n)
        g.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        ev.append(g)
    if data.get("files"):
        ev += [Spacer(1, 6), Paragraph("Proof files saved on the test station:", st["small"])]
        ev += [Paragraph(esc(f), st["mono"]) for f in data["files"][:12]]
    if ev:
        if not isinstance(ev[0], KeepTogether):
            ev.insert(0, Paragraph("Evidence", st["h2"]))
        story += ev

    # ---- technician sign-off
    story.append(KeepTogether([
        Paragraph("Technician sign-off", st["h2"]),
        kv_table([("Technician", data.get("tech")), ("Test station", data.get("station")),
                  ("Testing started", fmt_time(data.get("started"))),
                  ("Testing completed", fmt_time(data.get("completed"))),
                  ("Software", data.get("software"))] +
                 ([("Notes", data["notes"])] if data.get("notes") else [])),
        Spacer(1, 6),
        Paragraph("I confirm this device was tested as recorded above.", st["small"]),
        Spacer(1, 18),
        Table([[Paragraph(esc(data.get("tech") or ""), st["value"]), "",
                Paragraph(esc(fmt_time(data.get("completed")).split("  ")[0]), st["value"])],
               [Paragraph("Technician", st["label"]), "", Paragraph("Date", st["label"])]],
              colWidths=[W * 0.5, W * 0.1, W * 0.4],
              style=TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.6, INK), ("LINEABOVE", (2, 1), (2, 1), 0.6, INK),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0)]))]))

    # ---- activity log
    act = data.get("activity", [])
    if act:
        rows = [[Paragraph(esc(t.strftime("%H:%M:%S") if isinstance(t, datetime.datetime) else t), st["mono"]),
                 Paragraph(esc(x), st["mono"])] for t, x in act[-250:]]
        lt = Table(rows, colWidths=[0.75 * inch, W - 0.75 * inch])
        lt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        story += [Paragraph(f"Activity log <font color='#6d6e70' size='8'>&nbsp;&nbsp;{len(act)} entries"
                            + (", last 250 shown" if len(act) > 250 else "") + "</font>", st["h2"]), lt]

    def page(c, doc):
        c.saveState()
        w, h = letter
        # header
        top = h - 0.55 * inch
        x = 0.6 * inch
        logo = data.get("logo")
        drew_logo = False
        if logo and os.path.exists(logo):
            try:
                from reportlab.lib.utils import ImageReader
                ir = ImageReader(logo)
                iw, ih = ir.getSize()
                s = min(2.3 * inch / iw, 0.64 * inch / ih)
                c.drawImage(ir, x, top - ih * s + 0.04 * inch, iw * s, ih * s, mask="auto")
                drew_logo = True
                if data.get("company_line"):
                    c.setFont(regular, 8)
                    c.setFillColor(MUTED)
                    c.drawString(x, top - 0.64 * inch, data["company_line"])
            except Exception:
                pass
        if not drew_logo:
            c.setFillColor(INK)
            c.setFont(bold, 14)
            c.drawString(x, top - 0.24 * inch, data.get("company") or "")
            c.setFont(regular, 9)
            c.setFillColor(MUTED)
            c.drawString(x, top - 0.44 * inch, data.get("company_line") or "")
        c.setFont(bold, 15)
        c.setFillColor(ACCENT)
        c.drawRightString(w - 0.6 * inch, top - 0.24 * inch, data.get("title") or "Device Test Certificate")
        c.setFont(regular, 8.5)
        c.setFillColor(MUTED)
        c.drawRightString(w - 0.6 * inch, top - 0.44 * inch, data.get("subtitle") or "")
        c.setStrokeColor(ACCENT)
        c.setLineWidth(2)
        c.line(0.6 * inch, top - 0.72 * inch, w - 0.6 * inch, top - 0.72 * inch)
        # footer
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(0.6 * inch, 0.62 * inch, w - 0.6 * inch, 0.62 * inch)
        c.setFont(regular, 7.5)
        c.setFillColor(MUTED)
        c.drawString(0.6 * inch, 0.45 * inch, (f"{data['company']}  ·  " if data.get("company") else "") +
                     f"Certificate {data.get('cert_id', '')}  ·  "
                                              f"Asset tag {data.get('asset_tag') or '-'}  ·  "
                                              f"Generated by {data.get('software', 'Bench Tested')}")
        c.drawRightString(w - 0.6 * inch, 0.45 * inch, f"Page {doc.page} of {total[0] or '?'}")
        c.restoreState()

    total = [0]

    def make(target):
        return SimpleDocTemplate(target, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=1.45 * inch, bottomMargin=0.85 * inch,
                            title=f"Device Test Certificate {data.get('cert_id', '')}",
                            author=data.get("tech") or "", subject=data.get("asset_tag") or "",
                            creator=data.get("software") or "Bench Tested")
    # two passes: the first counts the pages so the footer can say "Page 1 of 2"
    import copy
    import io
    first = make(io.BytesIO())
    first.build(copy.deepcopy(story), onFirstPage=page, onLaterPages=page)
    total[0] = first.page
    make(path).build(story, onFirstPage=page, onLaterPages=page)
    return path
