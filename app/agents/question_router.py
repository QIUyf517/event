"""第一阶段支持范围内的确定性问题识别与直接回答。"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.models.research import DocumentChunk, SignalResult
from app.monitoring.service import known_market_closed


SUPPORTED_EXAMPLES = "当前资金信号、最新价格或60日价格位置、最新份额或5个可用披露日份额变化、成交额热度、已入库公告"
SIGNAL_NAMES = {
    "positive": "偏积极",
    "moderately_positive": "温和积极",
    "neutral": "中性",
    "cautious": "偏谨慎",
    "insufficient_data": "数据不足",
}


def latest_expected_market_day(as_of_date: date) -> date:
    day = as_of_date
    while known_market_closed(day):
        day -= timedelta(days=1)
    return day


@dataclass(frozen=True)
class QuestionPlan:
    focus: tuple[str, ...]
    document_types: tuple[str, ...] | None = None
    unsupported_reason: str | None = None
    scope_note: str | None = None
    asks_cause: bool = False

    @property
    def wants_documents(self) -> bool:
        return "documents" in self.focus and self.unsupported_reason is None


def plan_question(question: str, etf_code: str) -> QuestionPlan:
    text = question.strip()
    if not text:
        raise ValueError("请输入研究问题")

    other_codes = set(re.findall(r"(?<!\d)[15]\d{5}(?!\d)", text)) - {etf_code}
    if other_codes:
        return QuestionPlan((), unsupported_reason=f"问题涉及其他ETF（{', '.join(sorted(other_codes))}）；请先选择对应研究标的。")
    unsupported_topics = ("预测", "明天", "买入", "卖出", "收益率", "涨跌幅", "涨跌", "融资", "折溢价", "比较", "对比", "净流入")
    if any(term in text for term in unsupported_topics):
        return QuestionPlan((), unsupported_reason="当前版本没有计算该问题所需的指标或比较结果。")

    focus = []
    if any(term in text for term in ("资金信号", "综合信号", "综合分析", "资金行为")):
        focus.append("signal")
    if any(term in text for term in ("价格", "收盘", "行情", "区间")):
        focus.append("price")
    if any(term in text for term in ("份额", "申购", "赎回")):
        focus.append("shares")
    if any(term in text for term in ("成交", "热度", "活跃", "分位")):
        focus.append("turnover")
    if any(term in text for term in ("公告", "报告", "资料", "文件", "政策", "原文", "证据", "做市")):
        focus.append("documents")
    if not focus:
        return QuestionPlan((), unsupported_reason="当前版本无法识别这个问题的研究目标。")

    document_types = None
    if "定期报告" in text or "年报" in text or "中期报告" in text:
        document_types = ("fund_report",)
    elif "公告" in text:
        document_types = ("fund_announcement",)
    elif "政策" in text:
        document_types = ("policy",)

    scope_note = None
    period = re.search(r"(?:最近|近)\s*(\d+)\s*(?:个)?(交易日|披露日|天|日)", text)
    if period:
        count = int(period.group(1))
        if "shares" in focus and (count != 5 or period.group(2) == "交易日"):
            return QuestionPlan(tuple(focus), unsupported_reason="当前仅计算最近5个可用份额披露日的变化，不能替代指定交易日区间的变化。")
        if "price" in focus and count != 60:
            return QuestionPlan(tuple(focus), unsupported_reason="当前价格指标是近60个可用交易日的区间位置，不能替代指定期限的价格变化。")
        if "turnover" in focus:
            return QuestionPlan(tuple(focus), unsupported_reason="当前成交额热度使用全部可用历史样本，尚不支持指定期限的成交额统计。")
        if "signal" in focus:
            scope_note = f"目前只有截至查询日的综合信号截面，尚未计算最近{count}个交易日的逐日信号序列。"

    return QuestionPlan(tuple(focus), document_types, scope_note=scope_note, asks_cause=any(term in text for term in ("原因", "为什么", "解释", "导致")))


def answer_question(plan: QuestionPlan, signal: SignalResult, documents: list[DocumentChunk]) -> tuple[str, str]:
    if plan.unsupported_reason:
        return "unsupported", f"{plan.unsupported_reason} 目前支持：{SUPPORTED_EXAMPLES}。"

    values = signal.indicators
    latest = signal.latest_values
    market_date = signal.source_dates.get("market")
    share_date = signal.source_dates.get("shares")
    expected_day = latest_expected_market_day(signal.as_of_date) if signal.data_mode == "live" else None
    stale_market = expected_day is not None and (market_date is None or market_date < expected_day)
    stale_shares = expected_day is not None and (share_date is None or share_date < expected_day)
    parts = []
    incomplete = False
    if "signal" in plan.focus:
        if signal.signal == "insufficient_data":
            parts.append(f"截至{signal.as_of_date}，数据不足，无法形成完整资金信号；缺少：{', '.join(signal.missing_fields)}。")
            incomplete = True
        elif stale_market or stale_shares:
            parts.append(
                f"现有数据形成的综合信号为{SIGNAL_NAMES[signal.signal]}；行情截至{market_date}，"
                f"份额截至{share_date}。尚未取得查询日所需的完整数据，不能确认当前信号。"
            )
            incomplete = True
        else:
            parts.append(f"截至{market_date}的综合信号为{SIGNAL_NAMES[signal.signal]}。行情截至{market_date}，份额截至{share_date}。")
    if "price" in plan.focus:
        if latest.get("close") is None or values.get("price_position_60d") is None:
            parts.append("价格数据不足，无法回答价格问题。")
            incomplete = True
        else:
            parts.append(f"{market_date}收盘价为{latest['close']:.4f}元，处于最近60个可用交易日价格区间的{values['price_position_60d']:.1%}位置。")
    if "shares" in plan.focus:
        if latest.get("shares") is None:
            parts.append("份额数据不足，无法回答份额问题。")
            incomplete = True
        else:
            parts.append(f"{share_date}已披露份额为{latest['shares']:,.0f}份。")
            change = values.get("share_change_5d")
            if change is not None:
                parts.append(f"最近5个可用披露日变化{change:+.2%}；这不等于资金净流入额。")
            else:
                parts.append("可用份额记录不足，无法计算5个可用披露日变化。")
                incomplete = True
    if "turnover" in plan.focus:
        if latest.get("turnover") is None or values.get("turnover_percentile") is None:
            parts.append("成交额数据不足，无法回答成交额问题。")
            incomplete = True
        else:
            parts.append(f"{market_date}成交额为{latest['turnover']:,.0f}元，在现有{min(signal.source_records.get('market', 0), 252)}个历史样本中的分位为{values['turnover_percentile']:.1%}。")
    if "documents" in plan.focus:
        if documents:
            first = documents[0]
            parts.append(f"已入库资料命中{len(documents)}个片段；其中《{first.title}》的入库发布日期为{first.published_at} [D1]。")
        else:
            parts.append("已入库资料中没有找到符合问题与日期范围的文档，无法确认相关公开事实。")
            incomplete = True
    if plan.asks_cause:
        parts.append("现有资料不能证明当前价格或份额变化的成因。")
        incomplete = True
    if plan.scope_note:
        parts.append(plan.scope_note)
        incomplete = True
    if "signal" not in plan.focus and (
        (stale_market and ("price" in plan.focus or "turnover" in plan.focus))
        or (stale_shares and "shares" in plan.focus)
    ):
        parts.append("相关数据尚未更新至查询日，仅能说明已披露的历史记录。")
        incomplete = True
    return ("partial" if incomplete else "answered"), " ".join(parts)
