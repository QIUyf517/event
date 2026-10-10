import html
import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from app.agents.single_agent import SingleResearchAgent
from app.agents.llm_research import ModelDrivenResearchAgent
from app.config import ALGORITHM_VERSION, ALLOWED_ETFS, DATA_MODE, SETTINGS_DATABASE_PATH, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo, seed_profile
from app.data.sync import sync_market_data
from app.monitoring.service import compare_etfs, run_monitor_once
from app.reporting.pdf_export import render_report_pdf, report_pdf_filename
from app.tools.research_tools import ResearchTools


st.set_page_config(page_title="A股ETF研究助手", page_icon="研", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
<style>
    :root {
        --ink: #132c31; --ink-soft: #496268; --jade: #147a68; --jade-deep: #0c5e52;
        --jade-pale: #e8f2ef; --amber: #b47820; --paper: #fbfcfa; --line: #dbe5e2;
    }
    html, body, [class*="css"] { font-family: "Noto Sans SC", "Microsoft YaHei", "PingFang SC", sans-serif; }
    .stApp { background: var(--paper); color: var(--ink); }
    .block-container { max-width: 1160px; padding-top: 2.7rem; padding-bottom: 5rem; }

    .masthead { border-top: 4px solid var(--ink); padding: 1.3rem 0 1.65rem; margin-bottom: 1rem; }
    .masthead-kicker, .section-kicker {
        color: var(--jade); font-size: .72rem; font-weight: 750; letter-spacing: .16em;
        text-transform: uppercase; margin-bottom: .55rem;
    }
    .masthead h1 {
        color: var(--ink); font-family: "Noto Serif SC", "Songti SC", SimSun, serif;
        font-size: clamp(2rem, 4vw, 3.35rem); line-height: 1.18; letter-spacing: -.035em;
        margin: 0; max-width: 16ch;
    }
    .masthead-row { display: flex; justify-content: space-between; align-items: flex-end; gap: 2rem; }
    .masthead-copy { color: var(--ink-soft); line-height: 1.75; max-width: 35rem; margin: 0 0 .3rem; }
    .mode-chip {
        display: inline-flex; align-items: center; gap: .48rem; white-space: nowrap;
        border: 1px solid var(--line); padding: .45rem .72rem; font-size: .76rem;
        font-weight: 700; letter-spacing: .06em; color: var(--jade-deep); background: #fff;
    }
    .mode-chip::before { content: ""; width: .48rem; height: .48rem; border-radius: 50%; background: var(--jade); }
    .mode-chip.demo { color: #855910; }
    .mode-chip.demo::before { background: var(--amber); }

    .brief-line { border-top: 1px solid var(--line); margin: 1.25rem 0 0; }
    .research-hero { border-left: 4px solid var(--jade); padding: .2rem 0 .2rem 1.25rem; margin: .4rem 0 1.2rem; }
    .research-hero h2 {
        font-family: "Noto Serif SC", "Songti SC", SimSun, serif; font-size: clamp(1.45rem, 2.5vw, 2rem);
        line-height: 1.45; margin: 0 0 .48rem; color: var(--ink); letter-spacing: -.02em;
    }
    .research-hero p { font-size: 1rem; line-height: 1.8; margin: 0; color: var(--ink-soft); max-width: 78ch; }
    .meta-line { color: #667a7e; font-size: .83rem; margin-bottom: 1.5rem; letter-spacing: .015em; }
    .signal-stamp {
        display: inline-block; color: var(--jade-deep); background: var(--jade-pale); border-left: 3px solid var(--jade);
        padding: .3rem .62rem; font-size: .75rem; font-weight: 750; letter-spacing: .08em; margin-bottom: .85rem;
    }

    div[data-testid="stMetric"] { border-top: 2px solid var(--ink); padding: .85rem .1rem .2rem; }
    div[data-testid="stMetric"] label { color: var(--ink-soft); }
    div[data-testid="stMetricValue"] { color: var(--ink); font-family: "IBM Plex Mono", Consolas, monospace; }
    .metric-note { color: var(--ink-soft); font-size: .86rem; line-height: 1.65; min-height: 3.15rem; padding-right: .8rem; }
    div[data-testid="stButton"] button[kind="primary"] {
        background: var(--ink); border: 1px solid var(--ink); border-radius: 2px; font-weight: 700;
        min-height: 2.85rem; transition: transform .14s ease, background .14s ease;
    }
    div[data-testid="stButton"] button[kind="primary"]:hover {
        background: var(--jade-deep); border-color: var(--jade-deep); transform: translateY(-1px);
    }
    div[data-baseweb="select"] > div, textarea { border-radius: 2px !important; }
    div[data-testid="stTextArea"] textarea:focus, div[data-baseweb="select"] > div:focus-within {
        border-color: var(--jade) !important; box-shadow: 0 0 0 1px var(--jade) !important;
    }

    .section-head {
        display: flex; align-items: baseline; gap: .8rem; margin: 2.2rem 0 .8rem;
        border-bottom: 1px solid var(--line); padding-bottom: .62rem;
    }
    .section-head h3 {
        font-family: "Noto Serif SC", "Songti SC", SimSun, serif; color: var(--ink); font-size: 1.35rem; margin: 0;
    }
    .section-head span { color: #718489; font-size: .78rem; }
    .evidence-item { padding: 1rem 0 1.05rem; border-bottom: 1px solid var(--line); }
    .evidence-item strong { color: var(--ink); font-size: 1rem; }
    .evidence-meta { color: var(--jade-deep); font-size: .76rem; font-weight: 650; letter-spacing: .025em; margin: .3rem 0 .52rem; }
    .evidence-copy { color: var(--ink-soft); line-height: 1.78; }
    .inference-item { color: var(--ink-soft); line-height: 1.75; border-left: 2px solid #a8bbb7; padding-left: .9rem; margin: .65rem 0; }
    .chart-caption { color: #718489; font-size: .78rem; margin-top: -.25rem; }

    section[data-testid="stSidebar"] { background: #eef4f2; border-right: 1px solid var(--line); }
    section[data-testid="stSidebar"] h2 {
        font-family: "Noto Serif SC", "Songti SC", SimSun, serif; color: var(--ink);
    }
    .sidebar-label { color: var(--jade-deep); font-size: .7rem; font-weight: 750; letter-spacing: .14em; margin-bottom: .25rem; }
    .sidebar-value { color: var(--ink-soft); font-size: .8rem; word-break: break-all; margin-bottom: 1.15rem; }

    @media (prefers-color-scheme: dark) {
        :root { --ink: #e0ece9; --ink-soft: #aac0bc; --paper: #0d1819; --line: #29403d; --jade-pale: #173a35; }
        .stApp { background: var(--paper); }
        .mode-chip { background: #142324; }
        section[data-testid="stSidebar"] { background: #112021; }
        .evidence-meta, .masthead-kicker, .section-kicker { color: #62bca8; }
        div[data-testid="stButton"] button[kind="primary"] { background: #d5e5e1; color: #102625; border-color: #d5e5e1; }
    }
    @media (max-width: 700px) {
        .block-container { padding-top: 1.5rem; }
        .masthead-row { display: block; }
        .masthead-copy { margin-top: 1rem; }
        .mode-chip { margin-top: 1rem; }
        .metric-note { min-height: auto; margin-bottom: 1.2rem; }
    }
</style>
""",
    unsafe_allow_html=True,
)


SIGNAL_LABELS = {
    "positive": "偏积极",
    "moderately_positive": "温和积极",
    "neutral": "中性",
    "cautious": "偏谨慎",
    "insufficient_data": "数据不足",
}
INDICATOR_LABELS = {
    "price_position_60d": "近60日价格位置",
    "share_change_5d": "近5个可用披露日份额变化",
    "turnover_percentile": "成交额历史分位",
    "price_return_5d": "近5个可用收盘价变化",
    "volume_ratio_5d": "近5日成交量相对前5日",
}
STRENGTH_LABELS = {"strong": "较强", "medium": "中等", "weak": "较弱", "insufficient": "不足"}
MODE_LABELS = {"demo": "演示数据", "live": "真实数据"}
RUN_STATUS_LABELS = {"success": "已完成", "partial": "部分完成，需查看提示", "failed": "运行失败", "missing_data": "缺少行情数据"}
SYNC_STATUS_LABELS = {"not_requested": "仅检查本地数据", "market_closed": "休市日，未联网更新", "already_current": "本地行情已到查询日期", "success": "已取得新行情", "failed": "更新失败"}
SHARE_STATUS_LABELS = {"success": "已取得新份额记录", "partial": "只更新了部分份额记录", "failed": "更新失败", "unavailable": "暂未取得份额记录"}
TRACE_STATUS_LABELS = {"success": "完成", "failed": "失败", "skipped": "跳过"}
FIELD_LABELS = {
    "question": "研究问题", "etf_code": "基金代码", "as_of_date": "查询日期", "data_mode": "数据类型",
    "plan": "研究步骤", "focus": "问题重点", "document_types": "资料类型", "unsupported_reason": "暂不支持的原因",
    "error": "错误说明", "signal": "观察信号", "indicators": "观察指标", "source_dates": "数据来源日期",
    "source_records": "参与计算的记录数", "missing_fields": "缺少的指标", "market": "行情", "shares": "基金份额",
    "hit_count": "命中的资料数", "hits": "命中的资料", "chunk_id": "资料片段编号", "title": "标题",
    "published_at": "发布日期", "source_url": "原文链接", "source_category": "来源类型",
    "retained_citations": "保留的引用编号", "answer_status": "回答情况", "market_available": "有可用行情结果",
    "document_hits": "命中的资料数", "source_excerpts": "引用的资料片段", "verified_fact_count": "已核实事实数",
    "source_excerpt_count": "资料片段数", "source_quote_count": "逐字摘录数", "warnings": "提示",
    "sync_id": "更新记录编号", "start_date": "开始日期", "end_date": "结束日期", "market_status": "行情更新情况",
    "share_status": "份额更新情况", "market_rows": "新增行情记录数", "share_rows": "新增份额记录数",
    "data_cutoff_date": "共同数据截止日期", "etf": "基金", "name": "基金名称", "question_focus": "问题重点",
    "direct_answer": "直接回答", "calculated_results": "计算结果", "evidence_strength": "证据强度",
    "algorithm_version": "计算规则版本", "latest_values": "最新数值", "trigger_details": "指标作用说明",
    "price": "价格", "close": "收盘价", "volume": "成交量", "turnover": "成交额",
    "latest_signal": "最近的观察信号", "latest_market_date": "最新行情日期", "status": "运行情况",
    "run_id": "运行记录编号", "started_at": "开始时间", "finished_at": "完成时间",
    "etf_results": "各基金运行结果", "history_rows": "历史信号记录数", "sync_status": "数据更新情况",
    "agent": "研究角色", "duration_ms": "用时（毫秒）", "summary": "执行摘要", "input_summary": "输入摘要",
    "output_summary": "输出摘要", "evidence_refs": "引用编号", "created_at": "创建时间",
    "document_id": "资料编号", "content": "内容", "publisher": "发布方", "document_type": "资料类型",
    "page": "页码", "location": "原文位置", "citation": "引用编号", "classification": "信息分类",
    "source_quotes": "逐字摘录", "verified_facts": "已核实事实", "reasonable_inferences": "合理推测",
    "unverified_information": "暂时无法确认", "execution_trace": "执行过程", "disclaimer": "使用提醒",
    "contribution": "对信号的作用", "date_range": "查询日期范围", "evidence": "依据",
    "fund_company": "基金管理人", "index_name": "跟踪指数", "indicator": "指标", "query": "检索内容",
    "reason": "判断依据", "source": "数据来源", "value": "指标结果",
    "research_method": "研究方式", "tool_calls": "工具调用", "tool": "工具名称",
    "model_usage": "模型用量", "usage": "模型用量", "model": "模型名称",
    "total_tokens": "总词元数", "prompt_tokens": "输入词元数", "completion_tokens": "输出词元数",
    "prompt_tokens_details": "输入词元明细", "completion_tokens_details": "输出词元明细",
    "cached_tokens": "缓存词元数", "reasoning_tokens": "推理词元数",
    "prompt_cache_hit_tokens": "命中缓存的词元数", "prompt_cache_miss_tokens": "未命中缓存的词元数",
    "discarded_focus": "已排除的额外主题", "restored_focus": "已补齐的研究主题",
    "fallback": "按基金代码回退", "reviewed": "模型已复核", "supported": "核验认为有依据",
    "quotes_accepted": "通过逐字核对的摘录数", "blocked_by_scope": "问题超出范围",
}
VALUE_LABELS = {
    **SIGNAL_LABELS, **STRENGTH_LABELS, **MODE_LABELS, **TRACE_STATUS_LABELS,
    "answered": "已回答", "partial": "部分完成", "unsupported": "暂不支持",
    "not_requested": "未请求联网更新", "market_closed": "休市日未更新", "already_current": "本地数据已到查询日期",
    "unavailable": "暂不可用", "official": "已识别官方域名", "other": "其他来源", "fund_announcement": "基金公告",
    "fund_report": "基金报告", "policy": "政策资料", "index_document": "指数资料",
    "documents": "公开资料", "signal": "资金观察信号", "sse_fund_list": "上交所基金列表",
    "price_position_60d": INDICATOR_LABELS["price_position_60d"],
    "share_change_5d": INDICATOR_LABELS["share_change_5d"],
    "turnover_percentile": INDICATOR_LABELS["turnover_percentile"],
    "price_return_5d": INDICATOR_LABELS["price_return_5d"],
    "volume_ratio_5d": INDICATOR_LABELS["volume_ratio_5d"],
    "ai_collaboration": "AI 协作研究", "rules": "规则研究",
    "get_signal": "读取程序计算的指标", "search_documents": "检索已入库资料",
    "get_document_chunk": "读取资料片段",
}


def readable_text(value):
    if not isinstance(value, str):
        return value
    return VALUE_LABELS.get(value, value.replace(" Agent", "角色"))


def readable_details(value):
    """仅在页面展示时翻译键名和值；持久化结构保持原样。"""
    if isinstance(value, dict):
        return {INDICATOR_LABELS.get(key, FIELD_LABELS.get(key, key)): readable_details(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [readable_details(item) for item in value]
    return readable_text(value)


def readable_missing(keys):
    return "、".join(INDICATOR_LABELS.get(key, key) for key in keys) if keys else "无"


def readable_date(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or "暂无数据"


def indicator_value(key, value):
    if value is None:
        return "暂无数据"
    if key == "volume_ratio_5d":
        return f"{value:.2f}倍"
    return percent(value, signed=key in {"share_change_5d", "price_return_5d"})


def contribution_label(value):
    return "支持偏积极" if value > 0 else "支持偏谨慎" if value < 0 else "未形成明显倾向"


def percent(value, signed=False):
    if value is None:
        return "暂无数据"
    return f"{value:+.2%}" if signed else f"{value:.1%}"


def price_note(value):
    if value is None:
        return "缺少足够行情，无法判断价格位置。"
    if value <= 0.2:
        return "位于近60日价格区间底部附近。"
    if value <= 0.45:
        return "位于近60日价格区间中低位。"
    if value <= 0.65:
        return "位于近60日价格区间中部。"
    if value <= 0.8:
        return "位于近60日价格区间中高位。"
    return "位于近60日价格区间顶部附近。"


def turnover_note(value):
    if value is None:
        return "缺少成交额历史样本，无法判断活跃度。"
    level = "偏低" if value < 0.35 else "一般" if value < 0.65 else "偏高"
    return f"超过历史样本约{value:.0%}的交易日，成交活跃度{level}。"


def readable_summary(report):
    result = report["calculated_results"]
    values = result["indicators"]
    signal = SIGNAL_LABELS.get(result["signal"], result["signal"])
    strength = STRENGTH_LABELS.get(result["evidence_strength"], result["evidence_strength"])
    if result["signal"] == "insufficient_data":
        return "当前数据不足，不能形成可靠资金信号。", "系统保留缺失值，没有用零或演示数据替代。"
    position = percent(values.get("price_position_60d"))
    shares = percent(values.get("share_change_5d"), signed=True)
    heat = percent(values.get("turnover_percentile"))
    headline = f"{report['etf']['name']}当前综合信号为“{signal}”"
    detail = f"价格位于近60日区间的{position}，近5个可用披露日份额变化为{shares}，成交额热度处于历史{heat}分位。证据强度为{strength}。"
    return headline, detail


def section_heading(title, note):
    st.markdown(
        f'<div class="section-head"><h3>{html.escape(title)}</h3><span>{html.escape(note)}</span></div>',
        unsafe_allow_html=True,
    )


def render_operations_page(repo, active_mode, default_etf):
    st.markdown('<div class="section-kicker">维护已保存的数据</div>', unsafe_allow_html=True)
    history_etf = st.selectbox(
        "查看历史信号的基金",
        list(ALLOWED_ETFS),
        index=list(ALLOWED_ETFS).index(default_etf),
        format_func=lambda code: f"{code} · {ALLOWED_ETFS[code]}",
        key="operations_etf",
    )

    section_heading("三只基金的数据检查", "只在点击按钮后收集真实数据；打开网页仅显示已保存的结果")
    monitor_label = "手动收集并检查三只基金" if active_mode == "live" else "检查三只基金的演示数据（不联网）"
    if st.button(monitor_label, width="stretch"):
        with st.spinner("正在检查三只ETF的数据与信号……"):
            monitor_result = run_monitor_once(repo, date.today(), active_mode, sync_live=active_mode == "live")
            st.session_state[f"monitor_result_{active_mode}"] = monitor_result.model_dump(mode="json")
    monitor_result = st.session_state.get(f"monitor_result_{active_mode}")
    if monitor_result:
        run_label = RUN_STATUS_LABELS.get(monitor_result["status"], "状态未知")
        st.caption(f"上次手动检查：{monitor_result['finished_at']} · 整体情况：{run_label}。完成只表示检查流程结束，请以行情日期判断数据是否更新。")
        monitor_rows = []
        for item in monitor_result["etf_results"]:
            monitor_rows.append({
                "基金": f"{item['etf_code']} · {ALLOWED_ETFS.get(item['etf_code'], '名称未知')}",
                "检查结果": RUN_STATUS_LABELS.get(item["status"], "状态未知"),
                "行情更新": SYNC_STATUS_LABELS.get(item["sync_status"], "状态未知"),
                "份额更新": SHARE_STATUS_LABELS.get(item.get("share_sync_status"), "未请求联网更新"),
                "历史信号记录数": item.get("history_rows", 0),
                "最新观察信号": SIGNAL_LABELS.get(item.get("latest_signal"), "暂无结果"),
                "最新行情日期": item.get("latest_market_date") or "暂无数据",
                "最新份额日期": item.get("latest_share_date") or "暂无数据",
            })
        st.dataframe(pd.DataFrame(monitor_rows), width="stretch", hide_index=True)
        for item in monitor_result["etf_results"]:
            if item.get("error"):
                st.error(f"{item['etf_code']} 检查失败：{item['error']}")
        for warning in monitor_result["warnings"]:
            st.warning(warning)

    comparison = compare_etfs(repo, list(ALLOWED_ETFS), date.today(), active_mode)
    section_heading("三只基金对比", "同一张表查看各基金；行情或份额日期不同，请勿直接比较信号强弱")
    comparison_rows = []
    for item in comparison["rows"]:
        comparison_rows.append({"基金代码": item["etf_code"], "基金名称": item["name"],
            "观察信号": SIGNAL_LABELS.get(item["signal"], "暂无结果"),
            "行情截止日期": readable_date(item["source_dates"].get("market")),
            "份额截止日期": readable_date(item["source_dates"].get("shares")),
            "近60日价格位置": indicator_value("price_position_60d", item["indicators"].get("price_position_60d")),
            "近5次披露份额变化": indicator_value("share_change_5d", item["indicators"].get("share_change_5d")),
            "成交额历史分位": indicator_value("turnover_percentile", item["indicators"].get("turnover_percentile")),
            "近5日价格变化": indicator_value("price_return_5d", item["indicators"].get("price_return_5d")),
            "近5日成交量对比前5日": indicator_value("volume_ratio_5d", item["indicators"].get("volume_ratio_5d"))})
    st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)
    st.caption("价格位置和成交额分位越接近100%，表示在各自历史样本中越靠高位；成交量比高于1倍，表示最近5日比前5日更活跃。份额变化仅是申赎行为的间接线索。")
    for warning in comparison["warnings"]:
        st.warning(warning)

    section_heading("历史观察信号", "仅展示所选基金已保存的历史结果；每行按该日以前可用数据计算")
    history = repo.signal_history(history_etf, date.today() - timedelta(days=180), date.today(), ALGORITHM_VERSION)
    if history:
        st.dataframe(pd.DataFrame([{"观察日期": readable_date(item.as_of_date),
            "观察信号": SIGNAL_LABELS.get(item.signal, "暂无结果"),
            "支持偏积极的指标": sum(row["contribution"] > 0 for row in item.trigger_details),
            "支持偏谨慎的指标": sum(row["contribution"] < 0 for row in item.trigger_details),
            "行情截止日期": readable_date(item.source_dates.get("market")),
            "份额截止日期": readable_date(item.source_dates.get("shares")),
            "缺少的指标": readable_missing(item.missing_fields)} for item in history[-60:]]),
            width="stretch", hide_index=True)
    else:
        st.info("暂无当前算法版本的历史信号。点击上方手动检查后生成。")

    section_heading("本地提醒", "异常与信号变化保存在当前模式的数据库中")
    alerts = repo.recent_monitor_alerts(20)
    if alerts:
        for item in alerts:
            message = f"{item.etf_code} · {item.as_of_date} · {item.title}：{readable_text(item.message)}"
            if item.severity == "error":
                st.error(message)
            elif item.severity == "warning":
                st.warning(message)
            else:
                st.info(message)
    else:
        st.caption("当前没有本地提醒。")

    section_heading("运行记录", "查看手动检查和数据收集是否真正完成")
    log_tabs = st.tabs(["手动检查", "数据收集"])
    with log_tabs[0]:
        monitor_log = repo.recent_monitor_runs(20)
        if monitor_log:
            st.dataframe(pd.DataFrame([{"查询日期": item.as_of_date, "状态": item.status,
                "开始时间": item.started_at, "完成时间": item.finished_at,
                "基金数量": len(item.etf_results), "提示数量": len(item.warnings)} for item in monitor_log]),
                width="stretch", hide_index=True)
        else:
            st.caption("暂无监控检查记录。")
    with log_tabs[1]:
        sync_log = repo.recent_sync_runs(30)
        if sync_log:
            st.dataframe(pd.DataFrame([{"基金": item.etf_code, "开始日期": item.start_date,
                "结束日期": item.end_date, "行情状态": item.market_status,
                "份额状态": item.share_status, "行情记录": item.market_rows,
                "份额记录": item.share_rows, "提示数量": len(item.warnings)} for item in sync_log]),
                width="stretch", hide_index=True)
        else:
            st.caption("暂无数据同步记录。")

    if active_mode == "live":
        scheduled_log = repo.recent_scheduled_runs(20)
        if scheduled_log:
            with st.expander("停用前的自动任务历史"):
                st.dataframe(pd.DataFrame([{"日期": item.as_of_date, "时段": item.slot,
                    "状态": item.status, "开始时间": item.started_at, "完成时间": item.finished_at,
                    "说明": item.reason} for item in scheduled_log]), width="stretch", hide_index=True)


settings_repo = SQLiteRepository(SETTINGS_DATABASE_PATH)
user_settings = settings_repo.user_settings()
settings_repo.close()

with st.sidebar:
    st.markdown("## 页面导航")
    active_page = st.radio("页面", ("研究工作台", "数据与运行记录"))
    st.markdown("## 数据设置")
    active_mode = st.radio(
        "数据模式",
        ("demo", "live"),
        index=0 if user_settings.default_mode == "demo" else 1,
        format_func=lambda mode: "演示数据" if mode == "demo" else "真实数据",
        horizontal=True,
    )
    st.caption("打开或刷新网页只读取本地数据；点击手动收集按钮后才会联网更新日线与份额。")

mode_class = "demo" if active_mode == "demo" else ""
if active_page == "研究工作台":
    page_kicker = "公开数据 · 可复核的观察结果"
    page_title = "A股指数基金<br>研究工作台"
    page_copy = "用可复核的行情指标观察资金信号，以官方公开资料校验解释。结论、证据与未知信息分开呈现。"
else:
    page_kicker = "本地数据 · 更新状态与审计记录"
    page_title = "数据与<br>运行记录"
    page_copy = "检查三只基金的数据日期和历史信号，集中查看本地提醒、手动检查与数据收集记录。"
st.markdown(
    f"""
    <header class="masthead">
        <div class="masthead-kicker">{page_kicker}</div>
        <div class="masthead-row">
            <h1>{page_title}</h1>
            <div>
                <p class="masthead-copy">{page_copy}</p>
                <span class="mode-chip {mode_class}">{MODE_LABELS[active_mode]}</span>
            </div>
        </div>
    </header>
    """,
    unsafe_allow_html=True,
)

repo = SQLiteRepository(database_path_for_mode(active_mode))
if active_mode == "demo" and any(repo.profile(code) is None for code in ALLOWED_ETFS):
    seed_demo(repo, date.today())

with st.sidebar:
    st.markdown('<div class="sidebar-label">当前使用的数据</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sidebar-value">本地{MODE_LABELS[active_mode]}文件</div>', unsafe_allow_html=True)
    st.caption("真实数据与演示数据严格隔离。同步失败时不会自动混入演示数据。")
    sync_clicked = active_page == "研究工作台" and active_mode == "live" and st.button("手动收集所选基金数据", width="stretch")
    with st.expander("个人偏好"):
        preferred_etf = st.selectbox("默认研究标的", list(ALLOWED_ETFS),
            index=list(ALLOWED_ETFS).index(user_settings.default_etf),
            format_func=lambda code: f"{code} · {ALLOWED_ETFS[code]}", key="preferred_etf")
        preferred_question = st.text_area("默认研究问题", user_settings.default_question,
            height=88, key="preferred_question")
        preferred_page_size = st.slider("报告存档每页数量", 5, 50, user_settings.report_page_size, 5)
        if st.button("保存个人偏好", width="stretch"):
            settings_repo = SQLiteRepository(SETTINGS_DATABASE_PATH)
            settings_repo.save_user_settings(user_settings.model_copy(update={
                "default_etf": preferred_etf, "default_mode": active_mode,
                "default_question": preferred_question.strip() or user_settings.default_question,
                "report_page_size": preferred_page_size}))
            settings_repo.close()
            st.success("个人偏好已保存，下次打开页面时生效。")

if active_page == "数据与运行记录":
    render_operations_page(repo, active_mode, user_settings.default_etf)
    repo.close()
    st.stop()

st.markdown('<div class="section-kicker">生成新的研究简报</div>', unsafe_allow_html=True)
left, right = st.columns([1, 2.25], gap="large")
with left:
    etf_code = st.selectbox("研究标的", list(ALLOWED_ETFS),
        index=list(ALLOWED_ETFS).index(user_settings.default_etf),
        format_func=lambda code: f"{code} · {ALLOWED_ETFS[code]}")
    if active_mode == "live" and repo.profile(etf_code) is None:
        seed_profile(repo, etf_code)
with right:
    question = st.text_area(
        "研究问题",
        user_settings.default_question.replace("当前", f"{etf_code}当前", 1)
            if "当前" in user_settings.default_question and etf_code not in user_settings.default_question
            else user_settings.default_question,
        height=96,
        key=f"research_question_{etf_code}",
    )
    st.caption("可问：当前资金信号、最新价格或60日价格位置、份额变化、成交额热度、已入库公告。")

if sync_clicked:
    with st.status(f"正在同步{etf_code}的公开行情与份额数据……", expanded=True) as status:
        sync_result = sync_market_data(repo, etf_code, date.today().replace(month=1, day=1), date.today())
        st.write(f"行情：{SYNC_STATUS_LABELS.get(sync_result.market_status, sync_result.market_status)}，本次读取并处理 {sync_result.market_rows} 条记录。")
        st.write(f"基金份额：{SHARE_STATUS_LABELS.get(sync_result.share_status, '状态未知')}，本次读取并处理 {sync_result.share_rows} 条记录。")
        for warning in sync_result.warnings:
            st.warning(warning)
        with st.expander("查看本次更新的详细记录"):
            st.json(readable_details(sync_result.model_dump(mode="json")))
        status.update(label="更新已完成，请查看新增记录和提示", state="complete")

research_method = st.radio("研究方式", ["规则研究", "AI 协作研究"], horizontal=True,
    help="AI 协作研究会在点击生成后调用已配置的大模型；模型规划和检索，数字由程序计算，结论经过核验。")
if research_method == "AI 协作研究":
    st.caption("点击生成后才会向模型服务发送研究问题、程序计算结果和命中的本地资料片段；"
               "本次报告与工具记录保存在当前数据模式的本地数据库。模型服务可能产生费用。")
run_col, hint_col = st.columns([1, 3.5], vertical_alignment="center")
with run_col:
    run_research = st.button("生成研究简报", type="primary", width="stretch", disabled=not question.strip())
with hint_col:
    st.caption("输入具体问题后，系统会先直接回答；超出当前支持范围的问法会明确提示。")

if not question.strip():
    st.warning("请先输入研究问题。")

if run_research:
    with st.spinner("正在规划、读取证据并核验报告……"):
        try:
            agent = (ModelDrivenResearchAgent(ResearchTools(repo, active_mode))
                     if research_method == "AI 协作研究" else
                     SingleResearchAgent(ResearchTools(repo, active_mode)))
            st.session_state["latest_report"] = agent.run(question, etf_code).final_report
        except Exception as exc:
            st.error(f"本次研究未完成：{exc}")

report = st.session_state.get("latest_report")
selected_method = "ai_collaboration" if research_method == "AI 协作研究" else "rules"
if report and (report["etf"]["etf_code"] != etf_code or report["data_mode"] != active_mode
               or report.get("question") != question.strip()
               or report.get("research_method", "rules") != selected_method):
    st.session_state.pop("latest_report")
    report = None
if report:
    section_heading("研究过程", "展开查看每个研究角色完成的工作、输入和依据")
    excerpt_by_citation = {row["citation"]: row for row in report.get("source_excerpts", report.get("verified_facts", []))}
    for step in report.get("execution_trace", []):
        label = TRACE_STATUS_LABELS.get(step["status"], "未知状态")
        with st.expander(f"{readable_text(step['agent'])} · {label} · 用时 {step['duration_ms']} 毫秒 — {readable_text(step['summary'])}"):
            st.caption(f"开始：{step['started_at']} · 结束：{step['finished_at']}")
            if step.get("error"):
                st.error(f"运行遇到问题：{step['error']}")
            st.write("**输入摘要**")
            st.json(readable_details(step.get("input_summary", {})))
            st.write("**输出摘要**")
            st.json(readable_details(step.get("output_summary", {})))
            if step.get("evidence_refs"):
                st.write("**证据关联**")
                for ref in step["evidence_refs"]:
                    excerpt = excerpt_by_citation.get(ref)
                    if excerpt:
                        st.write(f"[{ref}] {excerpt['title']} · {excerpt['published_at']}")
                        if report["data_mode"] == "live":
                            st.link_button(f"打开 [{ref}] 原文 ↗", excerpt["source_url"])
                    else:
                        st.write(f"{ref} · 见下方程序计算的指标与数据日期")
if report and report["answer_status"] == "unsupported":
    section_heading("针对问题的回答", "当前支持范围")
    st.warning(report["direct_answer"])
elif report:
    st.markdown('<div class="brief-line"></div>', unsafe_allow_html=True)
    section_heading("针对问题的回答", "基于已入库数据与资料")
    st.info(report["direct_answer"])
    section_heading("核心结论" if "signal" in report["question_focus"] else "相关背景指标", "指标由程序计算，不代表投资建议")
    headline, detail = readable_summary(report)
    result = report["calculated_results"]
    signal_label = SIGNAL_LABELS.get(result["signal"], result["signal"])
    st.markdown(f'<div class="signal-stamp">综合信号 · {html.escape(signal_label)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="research-hero"><h2>{html.escape(headline)}</h2><p>{html.escape(detail)}</p></div>',
        unsafe_allow_html=True,
    )
    cutoff = report["data_cutoff_date"] or "暂无"
    source_dates = result["source_dates"]
    meta = (
        f"研究对象：{report['etf']['etf_code']} {report['etf']['name']}  ·  "
        f"行情：{source_dates.get('market', '暂无')}  ·  份额：{source_dates.get('shares', '暂无')}  ·  "
        f"共同截止：{cutoff}  ·  查询日期：{report['as_of_date']}  ·  使用：{MODE_LABELS[report['data_mode']]}"
    )
    st.markdown(f'<div class="meta-line">{html.escape(meta)}</div>', unsafe_allow_html=True)

    values = result["indicators"]
    col1, col2, col3 = st.columns(3, gap="large")
    with col1:
        st.metric("近60日价格位置", percent(values.get("price_position_60d")))
        st.markdown(
            f'<div class="metric-note">{html.escape(price_note(values.get("price_position_60d")))}</div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.metric("近5个可用披露日份额变化", percent(values.get("share_change_5d"), signed=True))
        share_value = values.get("share_change_5d")
        if share_value is None:
            share_text = "份额数据不足，不能判断申赎变化。"
        elif share_value >= 0:
            share_text = "份额增加，可视为净申购的代理信号，但不能识别具体资金主体。"
        else:
            share_text = "份额减少，可视为净赎回的代理信号，但不能识别具体资金主体。"
        st.markdown(f'<div class="metric-note">{html.escape(share_text)}</div>', unsafe_allow_html=True)
    with col3:
        st.metric("成交额历史分位", percent(values.get("turnover_percentile")))
        st.markdown(
            f'<div class="metric-note">{html.escape(turnover_note(values.get("turnover_percentile")))}</div>',
            unsafe_allow_html=True,
        )

    extra_left, extra_right = st.columns(2, gap="large")
    with extra_left:
        st.metric("近5个可用收盘价变化", percent(values.get("price_return_5d"), signed=True))
    with extra_right:
        volume_ratio = values.get("volume_ratio_5d")
        st.metric("近5日成交量 / 前5日", f"{volume_ratio:.2f}倍" if volume_ratio is not None else "暂无数据")
    section_heading("五项指标如何影响信号", "成交活跃度只按价格变化方向辅助判断；这些指标不能直接证明资金流入或流出")
    trigger_details = result.get("trigger_details", [])
    if trigger_details:
        st.dataframe(pd.DataFrame([{"指标": INDICATOR_LABELS.get(item["indicator"], item["indicator"]),
            "结果": indicator_value(item["indicator"], item["value"]),
            "对信号的作用": contribution_label(item["contribution"]), "如何理解": item["reason"]} for item in trigger_details]),
            width="stretch", hide_index=True)
    else:
        st.info("五项指标尚不完整，未形成完整共振信号。")

    bars = repo.market(report["etf"]["etf_code"], date.fromisoformat(report["as_of_date"]), limit=90) if source_dates.get("market") else []
    if bars:
        section_heading("价格轨迹", "最近90个可用交易日 · 数据源原始收盘价")
        price_data = pd.DataFrame(
            {"交易日期": [bar.trade_date for bar in bars], "收盘价": [bar.close for bar in bars]}
        ).set_index("交易日期")
        st.line_chart(price_data, color="#147a68", height=260)
        st.markdown(
            f'<div class="chart-caption">图表截止 {html.escape(str(source_dates.get("market", "暂无")))}，共 {len(price_data)} 个交易日样本。</div>',
            unsafe_allow_html=True,
        )

    section_heading("公开资料片段", "命中片段仅证明入库内容，不自动证明市场变化成因")
    source_excerpts = report.get("source_excerpts", report.get("verified_facts", []))
    for doc in source_excerpts:
        location = f"第{doc['page']}页" if doc.get("page") else doc.get("location") or "正文"
        source_label = {"official": "已识别官方域名", "demo": "合成演示资料", "other": "其他来源，官方性质未核实"}.get(doc.get("source_category"), "来源未分类")
        evidence_meta = f"{doc['published_at']} · {location} · {source_label}"
        st.markdown(
            '<article class="evidence-item">'
            f'<strong>{html.escape(str(doc["title"]))}</strong>'
            f'<div class="evidence-meta">{html.escape(evidence_meta)}</div>'
            f'<div class="evidence-copy">{html.escape(str(doc["content"]))}</div>'
            "</article>",
            unsafe_allow_html=True,
        )
        if report["data_mode"] == "live":
            st.link_button("打开来源原文 ↗", doc["source_url"])
        else:
            st.caption("合成演示资料，无真实原文链接。")
    if not source_excerpts:
        st.info("未检索到可展示的公开资料片段。系统不会自行补充事件解释。")

    section_heading("原文摘录与未知", "逐字摘录不等于对资金信号成因的证明")
    for quote in report.get("source_quotes", []):
        st.markdown(f'<div class="inference-item">{html.escape(str(quote))}</div>', unsafe_allow_html=True)
    if not report.get("source_quotes"):
        st.info("当前没有额外的逐字原文摘录；系统没有自动发布因果推断。")
    if report["unverified_information"]:
        st.markdown("**暂时无法确认**")
        for item in report["unverified_information"]:
            st.write(f"— {readable_text(item)}")

    section_heading("局限和边界", "阅读结论前请先确认这些限制")
    for warning in report["warnings"]:
        st.warning(readable_text(warning))
    st.info(report["disclaimer"])
    with st.expander("查看研究报告的完整记录"):
        st.caption("这里保留研究问题、计算结果、资料来源和研究过程，便于逐项核对。")
        st.json(readable_details(report))
    st.download_button("下载当前报告PDF", data=render_report_pdf(report),
        file_name=report_pdf_filename(report), mime="application/pdf", width="stretch")
else:
    st.markdown('<div class="brief-line"></div>', unsafe_allow_html=True)
    st.info("输入研究问题并生成简报后，这里会先给出针对问题的回答，再展示可核对的指标和资料。")

section_heading("报告存档", "研究结果自动保存，可查看结构化记录并导出JSON或PDF")
archive = repo.list_reports(user_settings.report_page_size)
if archive:
    st.dataframe(pd.DataFrame([{"报告编号": item["task_id"], "生成时间": item["created_at"],
        "基金": f"{item.get('etf_code') or '未知'} · {item.get('etf_name') or '名称未知'}",
        "查询日期": item.get("as_of_date"), "数据截止日期": item.get("data_cutoff_date") or "暂无",
        "问题": item.get("question"), "回答状态": item.get("answer_status")} for item in archive]),
        width="stretch", hide_index=True)
    archive_by_id = {item["task_id"]: item for item in archive}
    selected_report_id = st.selectbox("选择一份存档", list(archive_by_id),
        format_func=lambda task_id: f"{archive_by_id[task_id]['created_at']} · {archive_by_id[task_id].get('etf_code')} · {archive_by_id[task_id]['question'][:36]}")
    archived = repo.get_report(selected_report_id)
    if archived:
        export_left, export_right = st.columns(2)
        with export_left:
            st.download_button("下载存档JSON", data=json.dumps(archived, ensure_ascii=False, indent=2),
                file_name=f"ETF-research-{selected_report_id}.json", mime="application/json",
                width="stretch")
        with export_right:
            st.download_button("下载存档PDF", data=render_report_pdf(archived["report"], selected_report_id),
                file_name=report_pdf_filename(archived["report"]), mime="application/pdf",
                width="stretch")
        with st.expander("查看所选存档摘要"):
            st.write(archived["report"].get("direct_answer", "暂无回答"))
            st.caption(f"报告编号：{selected_report_id} · 保存时间：{archived['created_at']}")
else:
    st.caption("当前数据模式还没有研究报告存档。")

repo.close()
