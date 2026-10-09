"""将结构化研究报告导出为可阅读、可复核的PDF。"""

from __future__ import annotations

import html
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import PDF_FONT_PATH


INDICATOR_LABELS = {
    "price_position_60d": "近60日价格位置",
    "share_change_5d": "近5个可用披露日份额变化",
    "turnover_percentile": "成交额历史分位",
    "price_return_5d": "近5个可用收盘价变化",
    "volume_ratio_5d": "近5日成交量相对前5日",
}
SIGNAL_LABELS = {
    "positive": "偏积极", "moderately_positive": "温和积极", "neutral": "中性",
    "cautious": "偏谨慎", "insufficient_data": "数据不足",
}


def _font() -> str:
    candidates = [Path(PDF_FONT_PATH)] if PDF_FONT_PATH else []
    candidates.extend([
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ])
    for path in candidates:
        if path.is_file():
            try:
                name = "ETFReportFont"
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
                return name
            except Exception:
                continue
    name = "STSong-Light"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value)).replace("\n", "<br/>")


def _format_indicator(key: str, value: float | None) -> str:
    if value is None:
        return "暂无数据"
    if key == "volume_ratio_5d":
        return f"{value:.2f}倍"
    if key in {"share_change_5d", "price_return_5d"}:
        return f"{value:+.2%}"
    return f"{value:.1%}"


def render_report_pdf(report: dict, task_id: str | None = None) -> bytes:
    font = _font()
    palette = {"ink": colors.HexColor("#173238"), "jade": colors.HexColor("#147A68"),
               "soft": colors.HexColor("#546D72"), "line": colors.HexColor("#D7E2DF"),
               "pale": colors.HexColor("#EDF5F2")}
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ETFTitle", parent=styles["Title"], fontName=font, fontSize=22,
        leading=30, textColor=palette["ink"], spaceAfter=7 * mm)
    subtitle = ParagraphStyle("ETFSubtitle", parent=styles["Heading2"], fontName=font,
        fontSize=13, leading=19, textColor=palette["jade"], spaceBefore=5 * mm, spaceAfter=3 * mm)
    body = ParagraphStyle("ETFBody", parent=styles["BodyText"], fontName=font,
        fontSize=9.5, leading=16, textColor=palette["ink"], spaceAfter=2.5 * mm)
    small = ParagraphStyle("ETFSmall", parent=body, fontSize=8, leading=12, textColor=palette["soft"])
    answer = ParagraphStyle("ETFAnswer", parent=body, fontSize=11, leading=18,
        leftIndent=5 * mm, borderColor=palette["jade"], borderWidth=2, borderPadding=4 * mm,
        backColor=palette["pale"], spaceAfter=5 * mm)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=19 * mm, bottomMargin=17 * mm, title=report.get("title", "ETF研究报告"),
        author="A股ETF公开资料与资金信号研究助手")
    story = []
    etf = report.get("etf") or {}
    result = report.get("calculated_results") or {}
    source_dates = result.get("source_dates") or {}

    story.append(Paragraph(_safe(report.get("title", "ETF研究报告")), title))
    meta = [
        ["研究对象", f"{etf.get('etf_code', '')} {etf.get('name', '')}", "数据模式", report.get("data_mode", "")],
        ["查询日期", report.get("as_of_date", ""), "共同截止", report.get("data_cutoff_date") or "暂无"],
        ["行情截止", source_dates.get("market", "暂无"), "份额截止", source_dates.get("shares", "暂无")],
    ]
    if task_id:
        meta.append(["报告编号", task_id, "回答状态", report.get("answer_status", "")])
    meta_table = Table([[Paragraph(_safe(cell), small) for cell in row] for row in meta],
        colWidths=[24 * mm, 60 * mm, 24 * mm, 52 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, palette["line"]),
        ("BACKGROUND", (0, 0), (0, -1), palette["pale"]),
        ("BACKGROUND", (2, 0), (2, -1), palette["pale"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    direct_answer = report.get("direct_answer") or "这份历史报告未保存直接回答；以下仅展示当时归档的指标和资料片段。"
    story.extend([meta_table, Spacer(1, 6 * mm), Paragraph("针对问题的回答", subtitle),
                  Paragraph(_safe(direct_answer), answer)])

    story.append(Paragraph("五项观察指标", subtitle))
    indicator_rows = [[Paragraph("指标", small), Paragraph("结果", small), Paragraph("对信号的作用", small)]]
    details = {item.get("indicator"): item for item in result.get("trigger_details", [])}
    for key in INDICATOR_LABELS:
        item = details.get(key, {})
        contribution = item.get("contribution")
        direction = "支持偏积极" if contribution and contribution > 0 else "支持偏谨慎" if contribution and contribution < 0 else "未形成明显倾向"
        indicator_rows.append([Paragraph(_safe(INDICATOR_LABELS[key]), body),
            Paragraph(_safe(_format_indicator(key, (result.get("indicators") or {}).get(key))), body),
            Paragraph(_safe(direction), body)])
    indicator_table = Table(indicator_rows, colWidths=[72 * mm, 38 * mm, 50 * mm], repeatRows=1)
    indicator_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), palette["ink"]),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, palette["line"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([indicator_table, Spacer(1, 3 * mm), Paragraph(
        f"综合信号：{_safe(SIGNAL_LABELS.get(result.get('signal'), result.get('signal', '暂无')))}；算法版本：{_safe(result.get('algorithm_version', '暂无'))}。", small)])

    if result.get("trigger_details"):
        story.append(Paragraph("指标触发解释", subtitle))
        for item in result["trigger_details"]:
            story.append(Paragraph(f"· {_safe(INDICATOR_LABELS.get(item.get('indicator'), item.get('indicator')))}：{_safe(item.get('reason'))}", body))

    # v0.1 archives stored cited document snippets in evidence, before source_excerpts existed.
    excerpts = report.get("source_excerpts") or report.get("evidence") or []
    story.append(Paragraph("公开资料与引用", subtitle))
    if excerpts:
        for item in excerpts:
            location = f"第{item['page']}页" if item.get("page") else item.get("location") or "正文"
            block = [Paragraph(f"[{_safe(item.get('citation'))}] {_safe(item.get('title'))}", body),
                Paragraph(f"{_safe(item.get('published_at'))} · {_safe(location)} · {_safe(item.get('source_url'))}", small),
                Paragraph(_safe(item.get("content")), small), Spacer(1, 2 * mm)]
            story.append(KeepTogether(block))
    else:
        story.append(Paragraph("未检索到可展示的公开资料片段。", body))

    for heading, values in (
        ("暂时无法确认", report.get("unverified_information") or []),
        ("风险提示与局限", report.get("warnings") or []),
    ):
        story.append(Paragraph(heading, subtitle))
        if values:
            for value in values:
                story.append(Paragraph(f"· {_safe(value)}", body))
        else:
            story.append(Paragraph("无。", body))

    traces = report.get("execution_trace") or []
    if traces:
        story.append(Paragraph("研究过程摘要", subtitle))
        trace_rows = [[Paragraph("角色", small), Paragraph("状态", small), Paragraph("用时", small), Paragraph("摘要", small)]]
        for item in traces:
            trace_rows.append([Paragraph(_safe(item.get("agent")), small), Paragraph(_safe(item.get("status")), small),
                Paragraph(f"{_safe(item.get('duration_ms'))} ms", small), Paragraph(_safe(item.get("summary")), small)])
        trace_table = Table(trace_rows, colWidths=[35 * mm, 22 * mm, 22 * mm, 81 * mm], repeatRows=1)
        trace_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), palette["ink"]), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, palette["line"]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(trace_table)

    story.extend([Spacer(1, 5 * mm), Paragraph(_safe(report.get("disclaimer", "本报告仅供研究参考，不构成投资建议。")), answer)])

    def page(canvas, current_doc):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(palette["soft"])
        canvas.drawString(18 * mm, 9 * mm, "A股ETF公开资料与资金信号研究助手")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {current_doc.page} 页")
        canvas.setStrokeColor(palette["line"])
        canvas.line(18 * mm, 13 * mm, 192 * mm, 13 * mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def report_pdf_filename(report: dict) -> str:
    etf = (report.get("etf") or {}).get("etf_code", "ETF")
    day = str(report.get("as_of_date", "report"))
    return f"ETF-research-{etf}-{day}.pdf"
