import html
import json
from datetime import date

import pandas as pd
import streamlit as st

from app.agents.single_agent import SingleResearchAgent
from app.config import DATA_MODE, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.data.sync import sync_market_data
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
STRENGTH_LABELS = {"strong": "较强", "medium": "中等", "weak": "较弱", "insufficient": "不足"}


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
    detail = f"价格位于近60日区间的{position}，近5日份额变化为{shares}，成交额热度处于历史{heat}分位。证据强度为{strength}。"
    return headline, detail


def section_heading(title, note):
    st.markdown(
        f'<div class="section-head"><h3>{html.escape(title)}</h3><span>{html.escape(note)}</span></div>',
        unsafe_allow_html=True,
    )


mode_class = "demo" if DATA_MODE == "demo" else ""
st.markdown(
    f"""
    <header class="masthead">
        <div class="masthead-kicker">PUBLIC DATA · DETERMINISTIC SIGNALS</div>
        <div class="masthead-row">
            <h1>A股ETF<br>研究工作台</h1>
            <div>
                <p class="masthead-copy">用可复核的行情指标观察资金信号，以官方公开资料校验解释。结论、证据与未知信息分开呈现。</p>
                <span class="mode-chip {mode_class}">{html.escape(DATA_MODE.upper())} 数据</span>
            </div>
        </div>
    </header>
    """,
    unsafe_allow_html=True,
)

repo = SQLiteRepository(database_path_for_mode(DATA_MODE))
if DATA_MODE == "demo" and repo.profile("510300") is None:
    seed_demo(repo, date.today())

with st.sidebar:
    st.markdown("## 研究设置")
    st.markdown('<div class="sidebar-label">当前数据库</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sidebar-value">{html.escape(str(database_path_for_mode(DATA_MODE).name))}</div>',
        unsafe_allow_html=True,
    )
    st.caption("真实数据与演示数据严格隔离。同步失败时不会自动混入演示数据。")
    if DATA_MODE == "live" and st.button("同步真实公开数据", use_container_width=True):
        with st.status("正在同步行情与交易日数据……", expanded=True) as status:
            sync_result = sync_market_data(repo, "510300", date.today().replace(month=1, day=1), date.today())
            st.json(sync_result.model_dump(mode="json"))
            status.update(label="同步任务已结束", state="complete")

st.markdown('<div class="section-kicker">NEW RESEARCH BRIEF</div>', unsafe_allow_html=True)
left, right = st.columns([1, 2.25], gap="large")
with left:
    etf_code = st.selectbox("研究标的", ["510300"], help="首个版本仅支持沪深300ETF。")
with right:
    question = st.text_area(
        "研究问题",
        "分析510300最近20个交易日的资金信号，并结合公开资料解释可能原因",
        height=96,
    )

run_col, hint_col = st.columns([1, 3.5], vertical_alignment="center")
with run_col:
    run_research = st.button("生成研究简报", type="primary", use_container_width=True)
with hint_col:
    st.caption("数值由程序计算；文字解释不得引入工具结果以外的事实。")

if run_research:
    with st.spinner("正在计算指标并核验公开资料……"):
        st.session_state["latest_report"] = SingleResearchAgent(ResearchTools(repo, DATA_MODE)).run(
            question, etf_code
        ).final_report

report = st.session_state.get("latest_report")
if report:
    st.markdown('<div class="brief-line"></div>', unsafe_allow_html=True)
    section_heading("核心结论", "结论来自确定性指标，不代表投资建议")
    headline, detail = readable_summary(report)
    result = report["calculated_results"]
    signal_label = SIGNAL_LABELS.get(result["signal"], result["signal"])
    st.markdown(f'<div class="signal-stamp">综合信号 · {html.escape(signal_label)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="research-hero"><h2>{html.escape(headline)}</h2><p>{html.escape(detail)}</p></div>',
        unsafe_allow_html=True,
    )
    cutoff = report["data_cutoff_date"] or "暂无"
    meta = (
        f"研究对象：{report['etf']['etf_code']} {report['etf']['name']}  ·  "
        f"数据截止：{cutoff}  ·  查询日期：{report['as_of_date']}  ·  数据模式：{report['data_mode'].upper()}"
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
        st.metric("近5日份额变化", percent(values.get("share_change_5d"), signed=True))
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

    bars = repo.market(report["etf"]["etf_code"], date.fromisoformat(report["as_of_date"]), limit=90)
    if bars:
        section_heading("价格轨迹", "最近90个可用交易日 · 后复权口径以数据源为准")
        price_data = pd.DataFrame(
            {"交易日期": [bar.trade_date for bar in bars], "收盘价": [bar.close for bar in bars]}
        ).set_index("交易日期")
        st.line_chart(price_data, color="#147a68", height=260)
        st.markdown(
            f'<div class="chart-caption">图表截止 {html.escape(str(cutoff))}，共 {len(price_data)} 个交易日样本。</div>',
            unsafe_allow_html=True,
        )

    section_heading("公开资料证据", "仅展示可回到原文的资料")
    for doc in report["verified_facts"]:
        location = f"第{doc['page']}页" if doc.get("page") else doc.get("location") or "正文"
        evidence_meta = f"{doc['published_at']} · {location}"
        st.markdown(
            '<article class="evidence-item">'
            f'<strong>{html.escape(str(doc["title"]))}</strong>'
            f'<div class="evidence-meta">{html.escape(evidence_meta)}</div>'
            f'<div class="evidence-copy">{html.escape(str(doc["content"]))}</div>'
            "</article>",
            unsafe_allow_html=True,
        )
        st.link_button("打开官方原文 ↗", doc["source_url"])
    if not report["verified_facts"]:
        st.info("未检索到足够的公开资料证据。系统不会自行补充事件解释。")

    section_heading("推测与未知", "解释层与已核实事实明确分开")
    for inference in report["reasonable_inferences"]:
        st.markdown(f'<div class="inference-item">{html.escape(str(inference))}</div>', unsafe_allow_html=True)
    if not report["reasonable_inferences"]:
        st.info("当前没有通过数字和引用核验的综合推测。")
    if report["unverified_information"]:
        st.markdown("**暂时无法确认**")
        for item in report["unverified_information"]:
            st.write(f"— {item}")

    section_heading("局限和边界", "阅读结论前请先确认这些限制")
    for warning in report["warnings"]:
        st.warning(warning)
    st.info(report["disclaimer"])
    with st.expander("审计入口 · 查看结构化报告 JSON"):
        st.code(json.dumps(report, ensure_ascii=False, indent=2), language="json")
else:
    st.markdown('<div class="brief-line"></div>', unsafe_allow_html=True)
    st.info("选择研究标的并生成简报后，这里会依次呈现核心结论、指标、价格轨迹和公开资料证据。")

repo.close()
