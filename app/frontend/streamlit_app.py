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
st.title("A股ETF公开资料与资金信号研究助手")
st.caption("第一阶段原型：日频数据、确定性指标、公开资料检索和带引用研究摘要")
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
    st.json({"etf": report["etf"], "as_of_date": report["as_of_date"], "data_cutoff_date": report["data_cutoff_date"], "data_mode": report["data_mode"], "calculated_results": report["calculated_results"]})
    st.subheader("公开资料")
    for doc in report["verified_facts"]:
        st.markdown(f"**{doc['title']}** — [{doc['source_url']}]({doc['source_url']})")
        st.write(doc["content"])
    if not report["verified_facts"]:
        st.info("未检索到足够的公开资料证据。")
    st.subheader("合理推测")
    for inference in report["reasonable_inferences"]:
        st.write(inference)
    st.subheader("局限和边界")
    for warning in report["warnings"]: st.warning(warning)
    st.info(report["disclaimer"])
    with st.expander("查看结构化报告 JSON"): st.code(json.dumps(report, ensure_ascii=False, indent=2), language="json")
repo.close()

