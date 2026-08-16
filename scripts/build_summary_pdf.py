"""Render the required two-page assessment summary as a polished PDF."""
from __future__ import annotations

from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "SUMMARY.md"
OUTPUT = ROOT / "output" / "pdf" / "Field_Intelligence_Assessment_Summary.pdf"

INK = colors.HexColor("#101828")
NAVY = colors.HexColor("#13233B")
GOLD = colors.HexColor("#C69A37")
GOLD_PALE = colors.HexColor("#F7EAC3")
SLATE = colors.HexColor("#344054")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#D8DEE8")
PAPER = colors.HexColor("#FBFAF6")
GREEN = colors.HexColor("#236B4B")


def _safe_inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', text)
    return text


def _page_chrome(canvas, doc) -> None:
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 7 * mm, width, 7 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, height - 7 * mm, 43 * mm, 7 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(0.7)
    canvas.line(15 * mm, 15 * mm, width - 15 * mm, 15 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.1)
    canvas.drawString(15 * mm, 9.5 * mm,
                      "BROADPEAK AI ENGINEER TECHNICAL ASSESSMENT")
    canvas.drawRightString(width - 15 * mm, 9.5 * mm,
                           f"KRISHNA GUPTHA YANDURI  |  {doc.page} / 2")
    canvas.restoreState()


def _styles():
    base = getSampleStyleSheet()
    return {
        "kicker": ParagraphStyle(
            "Kicker", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=7.2, leading=8.5, textColor=GOLD, tracking=1.4,
            spaceAfter=1.5 * mm,
        ),
        "title": ParagraphStyle(
            "Title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=25, leading=27, textColor=INK, alignment=TA_LEFT,
            spaceAfter=1.6 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=12.2, leading=15, textColor=GREEN, spaceAfter=3.2 * mm,
        ),
        "heading": ParagraphStyle(
            "Heading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12.4, leading=14.2, textColor=NAVY,
            spaceBefore=2.2 * mm, spaceAfter=1.3 * mm, keepWithNext=True,
        ),
        "subheading": ParagraphStyle(
            "Subheading", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=11.3, textColor=GREEN,
            spaceBefore=1.5 * mm, spaceAfter=0.7 * mm, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.55, leading=11.15, textColor=SLATE,
            spaceAfter=1.55 * mm, allowWidows=0, allowOrphans=0,
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.15, leading=10.45, textColor=SLATE,
            leftIndent=0, firstLineIndent=0, spaceAfter=0.8 * mm,
        ),
        "number": ParagraphStyle(
            "Number", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.1, leading=10.2, textColor=SLATE,
            leftIndent=0, firstLineIndent=0, spaceAfter=0.65 * mm,
        ),
        "metric_value": ParagraphStyle(
            "MetricValue", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=16, leading=17, textColor=NAVY, alignment=TA_CENTER,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=6.1, leading=7.2, textColor=MUTED, alignment=TA_CENTER,
            tracking=0.5,
        ),
        "flow_title": ParagraphStyle(
            "FlowTitle", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=6.1, leading=7.2, textColor=colors.white,
            alignment=TA_CENTER, tracking=0.35,
        ),
        "flow_detail": ParagraphStyle(
            "FlowDetail", parent=base["Normal"], fontName="Helvetica",
            fontSize=5.9, leading=7.1, textColor=SLATE,
            alignment=TA_CENTER,
        ),
        "closing": ParagraphStyle(
            "Closing", parent=base["BodyText"], fontName="Helvetica-BoldOblique",
            fontSize=9.5, leading=12.2, textColor=NAVY,
            borderColor=GOLD, borderWidth=0.8, borderPadding=7,
            backColor=GOLD_PALE, spaceBefore=2.2 * mm,
        ),
    }


def _metric_table(rows: list[str], styles) -> Table:
    metrics = []
    for row in rows:
        value, label = row.split("|", 1)
        metrics.append((value, label))
    cells = []
    for value, label in metrics:
        cells.append([
            Paragraph(_safe_inline(value), styles["metric_value"]),
            Paragraph(_safe_inline(label), styles["metric_label"]),
        ])
    grid = []
    for start in range(0, len(cells), 3):
        grid.append([
            Table([[cells[i][0]], [cells[i][1]]],
                  colWidths=[52 * mm], rowHeights=[6.2 * mm, 5 * mm],
                  style=TableStyle([
                      ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                      ("TOPPADDING", (0, 0), (-1, -1), 0),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                  ]))
            for i in range(start, min(start + 3, len(cells)))
        ])
    table = Table(grid, colWidths=[55.3 * mm] * 3, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
    ]))
    return table


def _flow_table(rows: list[str], styles) -> Table:
    stages = [row.split("|", 1) for row in rows]
    data = []
    for index, (title, detail) in enumerate(stages):
        box = Table(
            [[Paragraph(f"{index + 1}. {_safe_inline(title)}", styles["flow_title"])],
             [Paragraph(_safe_inline(detail), styles["flow_detail"]) ]],
            colWidths=[25.5 * mm], rowHeights=[5.2 * mm, 8.3 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BACKGROUND", (0, 1), (-1, 1), colors.white),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0.5 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5 * mm),
            ]),
        )
        data.append(box)
        if index < len(stages) - 1:
            data.append(Paragraph("&gt;", ParagraphStyle(
                f"FlowArrow{index}", parent=styles["metric_value"],
                fontSize=11, leading=12, textColor=GOLD, alignment=TA_CENTER,
            )))
    widths = []
    for index in range(len(data)):
        widths.append(25.5 * mm if index % 2 == 0 else 4.4 * mm)
    table = Table([data], colWidths=widths, hAlign="CENTER")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _parse(source: str, styles) -> list:
    lines = source.splitlines()
    story: list = []
    index = 0
    bullet_buffer: list[str] = []
    number_buffer: list[str] = []

    def flush_lists() -> None:
        nonlocal bullet_buffer, number_buffer
        if bullet_buffer:
            story.append(ListFlowable(
                [ListItem(Paragraph(_safe_inline(item), styles["bullet"]),
                          leftIndent=3 * mm) for item in bullet_buffer],
                bulletType="bullet", start="circle", leftIndent=4 * mm,
                bulletFontName="Helvetica", bulletFontSize=5.5,
                bulletColor=GOLD, spaceAfter=1.3 * mm,
            ))
            bullet_buffer = []
        if number_buffer:
            story.append(ListFlowable(
                [ListItem(Paragraph(_safe_inline(item), styles["number"]),
                          leftIndent=3 * mm) for item in number_buffer],
                bulletType="1", leftIndent=4.5 * mm, bulletFontName="Helvetica-Bold",
                bulletFontSize=7.5, bulletColor=GOLD, spaceAfter=1.1 * mm,
            ))
            number_buffer = []

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush_lists()
            index += 1
            continue
        if line.startswith("# "):
            index += 1
            continue
        if line.startswith("> "):
            flush_lists()
            story.append(Paragraph(_safe_inline(line[2:]), styles["subtitle"]))
        elif line == "---":
            flush_lists()
            story.append(PageBreak())
        elif line == ":::metrics":
            flush_lists()
            metric_rows = []
            index += 1
            while index < len(lines) and lines[index].strip() != ":::":
                if lines[index].strip():
                    metric_rows.append(lines[index].strip())
                index += 1
            story.extend([Spacer(1, 1.1 * mm), _metric_table(metric_rows, styles),
                          Spacer(1, 1.1 * mm)])
        elif line == ":::flow":
            flush_lists()
            flow_rows = []
            index += 1
            while index < len(lines) and lines[index].strip() != ":::":
                if lines[index].strip():
                    flow_rows.append(lines[index].strip())
                index += 1
            story.extend([Spacer(1, 0.6 * mm), _flow_table(flow_rows, styles),
                          Spacer(1, 1.2 * mm)])
        elif line.startswith("## "):
            flush_lists()
            story.append(Paragraph(_safe_inline(line[3:]), styles["heading"]))
        elif line.startswith("### "):
            flush_lists()
            story.append(Paragraph(_safe_inline(line[4:]), styles["subheading"]))
        elif line.startswith("- "):
            bullet_buffer.append(line[2:])
        elif re.match(r"^\d+\. ", line):
            number_buffer.append(re.sub(r"^\d+\. ", "", line))
        else:
            flush_lists()
            paragraph = [line]
            while index + 1 < len(lines):
                candidate = lines[index + 1].strip()
                if not candidate or candidate.startswith(("#", "> ", "- ", ":::")) \
                        or candidate == "---" or re.match(r"^\d+\. ", candidate):
                    break
                paragraph.append(candidate)
                index += 1
            joined = " ".join(paragraph)
            style = styles["closing"] if joined.startswith("**Field Intelligence shows") else styles["body"]
            story.append(KeepTogether([Paragraph(_safe_inline(joined), style)]))
        index += 1
    flush_lists()
    return story


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=19 * mm,
        title="Field Intelligence - AI Engineer Technical Assessment Summary",
        author="Krishna Guptha Yanduri",
        subject="Product outcome, engineering proof and rollout plan",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="summary", frames=[frame], onPage=_page_chrome))

    styles = _styles()
    story = [
        Paragraph("FIELD INTELLIGENCE  |  PRODUCT &amp; ENGINEERING STORY", styles["kicker"]),
        Paragraph("Field Intelligence", styles["title"]),
        HRFlowable(width="100%", thickness=1.2, color=GOLD, spaceBefore=0,
                   spaceAfter=2.4 * mm),
    ]
    story.extend(_parse(SOURCE.read_text(encoding="utf-8"), styles))
    doc.build(story)
    return OUTPUT


if __name__ == "__main__":
    print(build())
