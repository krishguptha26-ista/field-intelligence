"""Render the required two-page assessment summary as a polished PDF."""
from __future__ import annotations

from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer)


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "SUMMARY.md"
OUTPUT = ROOT / "output" / "pdf" / "Field_Intelligence_Assessment_Summary.pdf"

NAVY = colors.HexColor("#0B1728")
GOLD = colors.HexColor("#D6A53E")
SLATE = colors.HexColor("#344054")
MUTED = colors.HexColor("#667085")


def _safe_inline(text: str) -> str:
    replacements = {
        "—": " - ", "–": "-", "≤": "<=", "★": " star",
        "“": '"', "”": '"', "’": "'", "→": "->", "−": "-",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', text)
    return text


def _footer(canvas, doc) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(0.7)
    canvas.line(18 * mm, 16 * mm, width - 18 * mm, 16 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.4)
    canvas.drawString(18 * mm, 10.5 * mm,
                      "BroadPeak AI Engineer Technical Assessment | Field Intelligence")
    canvas.drawRightString(width - 18 * mm, 10.5 * mm,
                           f"Krishna Guptha Yanduri | {doc.page}")
    canvas.restoreState()


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    width, height = A4
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=17 * mm, bottomMargin=21 * mm,
        title="Field Intelligence - AI Engineer Technical Assessment Summary",
        author="Krishna Guptha Yanduri",
        subject="Approach, results and next steps",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="summary", frames=[frame], onPage=_footer))

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, leading=24, textColor=NAVY, alignment=TA_LEFT,
        spaceAfter=3 * mm,
    )
    kicker = ParagraphStyle(
        "Kicker", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8.7, leading=10.5, textColor=GOLD, spaceAfter=1.8 * mm,
        uppercase=True,
    )
    heading = ParagraphStyle(
        "Heading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, leading=15, textColor=NAVY,
        spaceBefore=3 * mm, spaceAfter=1.6 * mm, keepWithNext=True,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=11, leading=14.5, textColor=SLATE,
        spaceAfter=2.35 * mm, allowWidows=0, allowOrphans=0,
    )

    blocks = [block.strip() for block in SOURCE.read_text(encoding="utf-8").split("\n\n")
              if block.strip()]
    story = [
        Paragraph("BROADPEAK INVESTMENT GROUP | TECHNOLOGY &amp; ARTIFICIAL INTELLIGENCE", kicker),
        Paragraph("Field Intelligence", title),
        Paragraph("AI-assisted field audit POC - approach, measured results and next steps", body),
        Spacer(1, 0.8 * mm),
    ]
    for block in blocks[1:]:
        if block.startswith("## "):
            story.append(Paragraph(_safe_inline(block[3:]), heading))
        else:
            story.append(KeepTogether([
                Paragraph(_safe_inline(" ".join(block.splitlines())), body)
            ]))
    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    print(build())
