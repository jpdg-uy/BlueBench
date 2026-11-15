"""Shared look of the BlueBench PDF documents (guide, CLI and Python API manuals).

Needs reportlab, pillow and a DejaVu Sans TTF (Linux: fonts-dejavu; or set GUIDE_FONT_DIR).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
                                XPreformatted)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IMG = HERE / "img"
sys.path.insert(0, str(ROOT / "src"))
from bluebench import __version__  # noqa: E402

# Where releases live (GitHub Releases: one page per version).
REPO_URL = "github.com/jpdg-uy/BlueBench"
DOWNLOADS = REPO_URL + "/releases"
ZIP_NAME = "BlueBench-win64.zip"
EXE_NAME = "BlueBench.exe"

FONT_DIR = Path(os.environ.get("GUIDE_FONT_DIR", "/usr/share/fonts/truetype/dejavu"))
for name, file in (("Sans", "DejaVuSans.ttf"), ("Sans-Bold", "DejaVuSans-Bold.ttf"),
                   ("Sans-Oblique", "DejaVuSans-Oblique.ttf"), ("Mono", "DejaVuSansMono.ttf")):
    path = FONT_DIR / file
    if not path.exists() and name == "Sans-Oblique":
        path = FONT_DIR / "DejaVuSans.ttf"
    pdfmetrics.registerFont(TTFont(name, str(path)))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique",
                              boldItalic="Sans-Bold")

ACCENT = colors.HexColor("#0077C8")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5F6B7A")
TIP_BG = colors.HexColor("#EAF4FB")
WARN_BG = colors.HexColor("#FFF4E5")
RULE = colors.HexColor("#D5DCE3")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

body = ParagraphStyle("body", fontName="Sans", fontSize=9.6, leading=14, textColor=INK,
                      spaceAfter=5)
small = ParagraphStyle("small", parent=body, fontSize=8.4, leading=11.5, textColor=MUTED)
caption = ParagraphStyle("caption", parent=small, alignment=TA_CENTER, spaceBefore=3, spaceAfter=10)
h1 = ParagraphStyle("h1", fontName="Sans-Bold", fontSize=17, leading=22, textColor=ACCENT,
                    spaceBefore=12, spaceAfter=8)
h2 = ParagraphStyle("h2", fontName="Sans-Bold", fontSize=11.5, leading=15, textColor=INK,
                    spaceBefore=9, spaceAfter=4)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=3)
cell = ParagraphStyle("cell", parent=body, fontSize=8.8, leading=12, spaceAfter=0)
cell_b = ParagraphStyle("cell_b", parent=cell, fontName="Sans-Bold")


def P(text, style=body):
    return Paragraph(text, style)


def B(text):
    return Paragraph(text, bullet, bulletText="•")


def ui(text):
    """A label exactly as it appears in the app."""
    return f"<b>{text}</b>"


def code(text):
    return f"<font face='Mono' size='8.6'>{text}</font>"


def box(paras, bg=TIP_BG, label="Consejo"):
    content = [P(f"<b>{label}.</b> {paras[0]}", cell)] + [P(p, cell) for p in paras[1:]]
    t = Table([[content]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return KeepTogether([Spacer(1, 3), t, Spacer(1, 6)])


CROP_DIR = Path(tempfile.mkdtemp(prefix="guide-crops-"))


def shot(name, text, width=CONTENT_W, crop=None):
    """crop = (left, top, right, bottom) as fractions of the screenshot."""
    path = IMG / f"{name}.png"
    with PILImage.open(path) as im:
        if crop:
            W, H = im.size
            im = im.crop((int(crop[0] * W), int(crop[1] * H), int(crop[2] * W), int(crop[3] * H)))
            path = CROP_DIR / f"{name}.png"
            im.save(path)
        w, h = im.size
    width = min(width, CONTENT_W)
    img = Image(str(path), width=width, height=width * h / w)
    frame = Table([[img]], colWidths=[width + 2])
    frame.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether([Spacer(1, 4), frame, P(text, caption)])


def table(rows, widths, header=True):
    data = [[P(c, cell_b if (header and r == 0) else cell) for c in row]
            for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F4F7")),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT)]
    t.setStyle(TableStyle(style))
    return KeepTogether([t, Spacer(1, 8)])


CODE_BG = colors.HexColor("#F4F6F8")
code_style = ParagraphStyle("codeblock", fontName="Mono", fontSize=8.2, leading=10.8,
                            textColor=INK)


def _wrap(line: str, limit: int) -> list[str]:
    """Break a long line at spaces; continuation lines are indented."""
    out, indent = [], " " * (len(line) - len(line.lstrip()) + 4)
    while len(line) > limit:
        cut = line.rfind(" ", len(indent), limit)
        if cut <= len(indent):
            break
        out.append(line[:cut])
        line = indent + line[cut + 1:]
    return out + [line]


def codeblock(text, width=CONTENT_W, label=None):
    """Monospaced block that keeps spaces and line breaks (commands, output, Python).
    Lines too long for the width wrap at a space, with an indented continuation."""
    limit = int((width - 16) / (code_style.fontSize * 0.6))
    lines = [w for line in text.strip("\n").split("\n") for w in _wrap(line, limit)]
    esc = "\n".join(lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = Table([[XPreformatted(esc, code_style)]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    head = [Paragraph(label, body)] if label else [Spacer(1, 2)]
    return KeepTogether(head + [t, Spacer(1, 6)])


# -- document -------------------------------------------------------------------
PAGES: dict[str, int] = {}  # section title -> page, filled by the first build pass


class _Doc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "h1":
            PAGES[flowable.getPlainText()] = self.page


def contents(sections):
    """Table of contents for the cover; page numbers come from the first pass."""
    rows = [["Sección", "Página"]]
    rows += [[t, str(PAGES.get(t, ""))] for t in sections]
    return table(rows, [CONTENT_W - 25 * mm, 25 * mm])


def build_pdf(out: Path, *, subtitle: str, sections, story) -> None:
    """Cover + body pages. ``story()`` returns the cover flowables followed by
    the body; every entry of ``sections`` must appear as an h1 heading."""
    title = f"BlueBench · {subtitle}"

    def on_cover(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(ACCENT)
        canvas.rect(0, PAGE_H - 95 * mm, PAGE_W, 95 * mm, stroke=0, fill=1)
        icon = ROOT / "src" / "bluebench" / "data" / "icon.png"
        if icon.exists():
            canvas.drawImage(str(icon), MARGIN, PAGE_H - 58 * mm, 30 * mm, 30 * mm, mask="auto")
        canvas.setFillColor(colors.white)
        canvas.setFont("Sans-Bold", 30)
        canvas.drawString(MARGIN + 36 * mm, PAGE_H - 40 * mm, "BlueBench")
        canvas.setFont("Sans", 13)
        canvas.drawString(MARGIN + 36 * mm, PAGE_H - 50 * mm, subtitle)
        canvas.setFont("Sans", 10)
        canvas.drawString(MARGIN, PAGE_H - 82 * mm,
                          f"Versión {__version__}  ·  Windows 10 / 11  ·  noviembre de 2026")
        canvas.restoreState()

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, PAGE_H - 13 * mm, PAGE_W - MARGIN, PAGE_H - 13 * mm)
        canvas.setFont("Sans", 7.8)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, PAGE_H - 11 * mm, title)
        canvas.drawRightString(PAGE_W - MARGIN, 11 * mm, str(doc.page))
        canvas.restoreState()

    doc = _Doc(str(out), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
               topMargin=19 * mm, bottomMargin=18 * mm, title=title, author="BlueBench",
               subject=title)
    frame = Frame(MARGIN, 18 * mm, CONTENT_W, PAGE_H - 37 * mm, id="f", leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate("cover", [frame], onPage=on_cover),
                          PageTemplate("body", [frame], onPage=on_page)])
    PAGES.clear()
    # Two passes: the first records each section's page for the table of contents.
    doc.build(story())
    missing = set(sections) - set(PAGES)
    if missing:
        raise SystemExit(f"Sections without a heading: {missing}")
    doc.build(story())


def body_start():
    """Flowables that end the cover and switch to body pages."""
    return [NextPageTemplate("body"), PageBreak()]
