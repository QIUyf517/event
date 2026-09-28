import json
from datetime import date
import streamlit as st
from app.config import DATABASE_PATH
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.data.sync import sync_market_data
from app.agents.single_agent import SingleResearchAgent
from app.tools.research_tools import ResearchTools

st.set_page_config(page_title="A股ETF研究助手", layout="wide")
st.title("A股ETF公开资料与资金信号研究助手")
st.caption("第一阶段原型：日频数据、确定性指标、公开资料检索和带引用研究摘要")
repo = SQLiteRepository(DATABASE_PATH)
seed_demo(repo, date.today())
with st.sidebar:
    if st.button("尝试同步公开行情"):
        st.json(sync_market_data(repo, "510300", date.today(), date.today()))
etf_code = st.selectbox("ETF", ["510300"])
question = st.text_area("研究问题", "分析510300最近20个交易日的资金信号，并结合公开资料解释可能原因")
if st.button("开始研究", type="primary"):
    report = SingleResearchAgent(ResearchTools(repo)).run(question, etf_code).final_report
    st.subheader("核心结论")
    st.json({k: report[k] for k in ["etf", "as_of_date", "indicators", "signal", "evidence_strength"]})
    st.subheader("公开资料")
    for doc in report["public_documents"]:
        st.markdown(f"**{doc['title']}** — [{doc['source_url']}]({doc['source_url']})")
        st.write(doc["content"])
    st.subheader("局限和边界")
    for warning in report["warnings"]: st.warning(warning)
    st.info(report["disclaimer"])
    with st.expander("查看结构化报告 JSON"): st.code(json.dumps(report, ensure_ascii=False, indent=2), language="json")
repo.close()

