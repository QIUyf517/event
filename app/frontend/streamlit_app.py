import json
from datetime import date
import streamlit as st
from app.config import DATA_MODE, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.data.sync import sync_market_data
from app.agents.single_agent import SingleResearchAgent
from app.tools.research_tools import ResearchTools

st.set_page_config(page_title="A股ETF研究助手", layout="wide")
st.markdown("""
<style>
    .block-container {max-width: 1120px; padding-top: 2.2rem;}
    .research-hero {border-left: 5px solid #177e89; padding: .35rem 0 .35rem 1.15rem; margin: 1rem 0 1.4rem;}
    .research-hero h2 {font-size: 1.55rem; line-height: 1.45; margin: 0 0 .35rem; color: #183642;}
    .research-hero p {font-size: 1rem; line-height: 1.75; margin: 0; color: #425b63; max-width: 78ch;}
    .meta-line {color: #657b83; font-size: .92rem; margin-bottom: 1.1rem;}
    .metric-note {color: #536870; font-size: .9rem; line-height: 1.55; min-height: 3rem;}
    .evidence-item {padding: .9rem 1rem; border-left: 3px solid #9fb8c0; background: #f6f9fa; margin: .7rem 0;}
    @media (prefers-color-scheme: dark) {
        .research-hero h2 {color: #d8ebef;}
        .research-hero p, .meta-line, .metric-note {color: #a9c0c6;}
        .evidence-item {background: #18272c; border-left-color: #4f8993;}
    }
</style>
""", unsafe_allow_html=True)

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
    if value is None: return "缺少足够行情，无法判断价格位置。"
    if value <= .2: return "位于近60日价格区间底部附近。"
    if value <= .45: return "位于近60日价格区间中低位。"
    if value <= .65: return "位于近60日价格区间中部。"
    if value <= .8: return "位于近60日价格区间中高位。"
    return "位于近60日价格区间顶部附近。"

def turnover_note(value):
    if value is None: return "缺少成交额历史样本，无法判断活跃度。"
    level = "偏低" if value < .35 else "一般" if value < .65 else "偏高"
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

st.title("A股ETF公开资料与资金信号研究助手")
st.caption("用确定性指标观察资金行为，用官方资料核验可能原因")
repo = SQLiteRepository(database_path_for_mode(DATA_MODE))
if DATA_MODE == "demo" and repo.profile("510300") is None:
    seed_demo(repo, date.today())
st.badge(f"数据模式：{DATA_MODE.upper()}", color="orange" if DATA_MODE == "demo" else "green")
with st.sidebar:
    st.write(f"数据库：{database_path_for_mode(DATA_MODE).name}")
    if DATA_MODE == "live" and st.button("同步真实公开数据"):
        result = sync_market_data(repo, "510300", date.today().replace(month=1, day=1), date.today())
        st.json(result.model_dump(mode="json"))
etf_code = st.selectbox("ETF", ["510300"])
question = st.text_area("研究问题", "分析510300最近20个交易日的资金信号，并结合公开资料解释可能原因")
if st.button("开始研究", type="primary"):
    report = SingleResearchAgent(ResearchTools(repo, DATA_MODE)).run(question, etf_code).final_report
    st.subheader("核心结论")
    headline, detail = readable_summary(report)
    st.markdown(f'<div class="research-hero"><h2>{headline}</h2><p>{detail}</p></div>', unsafe_allow_html=True)
    cutoff = report["data_cutoff_date"] or "暂无"
    st.markdown(f'<div class="meta-line">研究对象：{report["etf"]["etf_code"]} · 数据截止：{cutoff} · 查询日期：{report["as_of_date"]} · 数据模式：{report["data_mode"].upper()}</div>', unsafe_allow_html=True)

    values = report["calculated_results"]["indicators"]
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("近60日价格位置", percent(values.get("price_position_60d")))
        st.markdown(f'<div class="metric-note">{price_note(values.get("price_position_60d"))}</div>', unsafe_allow_html=True)
    with col2:
        st.metric("近5日份额变化", percent(values.get("share_change_5d"), signed=True))
        share_value = values.get("share_change_5d")
        share_text = "份额数据不足，不能判断申赎变化。" if share_value is None else ("份额增加，可视为净申购的代理信号，但不能识别具体资金主体。" if share_value >= 0 else "份额减少，可视为净赎回的代理信号，但不能识别具体资金主体。")
        st.markdown(f'<div class="metric-note">{share_text}</div>', unsafe_allow_html=True)
    with col3:
        st.metric("成交额历史分位", percent(values.get("turnover_percentile")))
        st.markdown(f'<div class="metric-note">{turnover_note(values.get("turnover_percentile"))}</div>', unsafe_allow_html=True)

    st.subheader("公开资料")
    for doc in report["verified_facts"]:
        location = f"第{doc['page']}页" if doc.get("page") else doc.get("location") or "正文"
        st.markdown(f'<div class="evidence-item"><strong>{doc["title"]}</strong><br><small>{doc["published_at"]} · {location}</small><br>{doc["content"]}</div>', unsafe_allow_html=True)
        st.link_button("查看官方原文", doc["source_url"])
    if not report["verified_facts"]:
        st.info("未检索到足够的公开资料证据。")
    st.subheader("合理推测")
    for inference in report["reasonable_inferences"]:
        st.write(inference)
    if not report["reasonable_inferences"]:
        st.info("当前没有通过数字和引用核验的综合推测。")
    if report["unverified_information"]:
        st.subheader("暂时无法确认")
        for item in report["unverified_information"]:
            st.write(f"- {item}")
    st.subheader("局限和边界")
    for warning in report["warnings"]: st.warning(warning)
    st.info(report["disclaimer"])
    with st.expander("技术详情：查看结构化报告 JSON"):
        st.code(json.dumps(report, ensure_ascii=False, indent=2), language="json")
repo.close()

