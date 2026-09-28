from datetime import date, timedelta
from uuid import uuid4
from app.agents.verifier import verify_generated_inferences
from app.llm.client import OpenAICompatibleClient
from app.models.research import Evidence, ResearchReport, ResearchState
from app.tools.research_tools import ResearchTools

class SingleResearchAgent:
    """确定性工具编排器；后续可接入LLM，但不让模型计算数字。"""
    def __init__(self, tools: ResearchTools, llm: OpenAICompatibleClient | None = None):
        self.tools = tools
        self.llm = llm or OpenAICompatibleClient()

    def _call(self, state: ResearchState, tool_name: str, payload: dict, fn):
        call_id = str(uuid4())
        try:
            result = fn()
            serializable = result.model_dump(mode="json") if hasattr(result, "model_dump") else [x.model_dump(mode="json") for x in result] if isinstance(result, list) and result and hasattr(result[0], "model_dump") else result
            self.tools.repo.save_tool_call(call_id, state.task_id, tool_name, "success", payload, serializable)
            return result
        except Exception as exc:
            self.tools.repo.save_tool_call(call_id, state.task_id, tool_name, "failed", payload, {"error": str(exc)})
            raise

    def run(self, question: str, etf_code: str = "510300", as_of_date: date | None = None) -> ResearchState:
        as_of_date = as_of_date or date.today()
        state = ResearchState(task_id=str(uuid4()), question=question, etf_codes=[etf_code], as_of_date=as_of_date, data_mode=self.tools.data_mode, plan=["读取ETF基础信息", "计算确定性资金信号", "检索公开资料", "核验并生成报告"])
        self.tools.repo.save_task(state.task_id, question)
        profile = self._call(state, "get_etf_profile", {"etf_code": etf_code}, lambda: self.tools.get_etf_profile(etf_code))
        signal = self._call(state, "calculate_fund_signal", {"etf_code": etf_code, "as_of_date": as_of_date}, lambda: self.tools.calculate_fund_signal(etf_code, as_of_date))
        state.market_results = [signal.model_dump(mode="json")]
        state.evidence.append(Evidence(evidence_id=f"data-{state.task_id}", evidence_type="data", title="确定性资金信号计算结果", claim="指标由程序根据已入库市场数据计算", details=signal.model_dump(mode="json"), classification="calculation"))
        date_range = (as_of_date - timedelta(days=365), as_of_date)
        docs = self._call(state, "search_public_documents", {"query": question, "etf_code": etf_code, "date_range": date_range}, lambda: self.tools.search_public_documents(question, etf_code, date_range))
        state.document_results = docs
        verified_facts, evidence_rows = [], []
        for index, doc in enumerate(docs, start=1):
            citation = f"D{index}"
            state.evidence.append(Evidence(evidence_id=doc.chunk_id, evidence_type="document", title=doc.title, claim=doc.content, source_url=doc.source_url, source_date=doc.published_at, details={"citation": citation, "page": doc.page, "location": doc.location}, classification="verified_fact"))
            row = {"citation": citation, "title": doc.title, "content": doc.content, "source_url": doc.source_url, "published_at": doc.published_at.isoformat(), "page": doc.page, "location": doc.location}
            verified_facts.append(row)
            evidence_rows.append(row)
        if signal.missing_fields: state.warnings.append("部分指标缺少数据，系统未将缺失值填充为零。")
        state.warnings.append("份额变化仅是资金行为代理信号，不等同于资金净流入或特定主体买卖。")
        if not docs:
            state.warnings.append("未检索到足够的公开资料证据；系统不会补充未经核实的事件解释。")
        inferences = []
        if docs and self.llm.configured:
            try:
                generated = self.llm.summarize(question, {"signal": signal.model_dump(mode="json"), "documents": evidence_rows})
                inferences, verify_warnings = verify_generated_inferences(generated, {"signal": signal.model_dump(mode="json"), "documents": evidence_rows}, {row["citation"] for row in evidence_rows})
                state.warnings.extend(verify_warnings)
            except Exception as exc:
                state.warnings.append(f"LLM归纳失败，已保留确定性报告: {exc}")
        unverified = []
        if not docs:
            unverified.append("公开资料不足，无法解释信号原因")
        if signal.missing_fields:
            unverified.append(f"缺少指标数据，无法确认完整资金信号: {', '.join(signal.missing_fields)}")
        data_cutoff = min(signal.source_dates.values()) if signal.source_dates else None
        report = ResearchReport(data_mode=self.tools.data_mode, etf=profile.model_dump(mode="json") if profile else None, as_of_date=signal.as_of_date, data_cutoff_date=data_cutoff, verified_facts=verified_facts, calculated_results=signal.model_dump(mode="json"), reasonable_inferences=inferences, unverified_information=unverified, evidence=evidence_rows, warnings=state.warnings, disclaimer="本报告仅供研究参考，不构成投资建议，也不提供自动交易或个性化投资建议。")
        state.final_report = report.model_dump(mode="json")
        self.tools.repo.save_evidence(state.task_id, state.evidence)
        self.tools.repo.save_report(state.task_id, state.final_report)
        self.tools.repo.update_task_status(state.task_id, "completed")
        return state

