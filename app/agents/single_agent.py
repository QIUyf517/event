from datetime import date, timedelta
from uuid import uuid4
from app.models.research import Evidence, ResearchState
from app.tools.research_tools import ResearchTools

class SingleResearchAgent:
    """确定性工具编排器；后续可接入LLM，但不让模型计算数字。"""
    def __init__(self, tools: ResearchTools): self.tools = tools

    def run(self, question: str, etf_code: str = "510300", as_of_date: date | None = None) -> ResearchState:
        as_of_date = as_of_date or date.today()
        state = ResearchState(task_id=str(uuid4()), question=question, etf_codes=[etf_code], as_of_date=as_of_date, plan=["读取ETF基础信息", "计算确定性资金信号", "检索公开资料", "核验并生成报告"])
        self.tools.repo.save_task(state.task_id, question)
        profile = self.tools.get_etf_profile(etf_code)
        signal = self.tools.calculate_fund_signal(etf_code, as_of_date)
        state.market_results = [signal.model_dump()]
        state.evidence.append(Evidence(evidence_id=f"data-{state.task_id}", evidence_type="data", title="确定性资金信号计算结果", claim="指标由程序根据已入库市场数据计算", details=signal.model_dump()))
        docs = self.tools.search_public_documents(question, etf_code, (as_of_date - timedelta(days=365), as_of_date))
        state.document_results = docs
        for doc in docs:
            state.evidence.append(Evidence(evidence_id=doc.chunk_id, evidence_type="document", title=doc.title, claim=doc.content, source_url=doc.source_url, source_date=doc.published_at))
        if signal.missing_fields: state.warnings.append("部分指标缺少数据，系统未将缺失值填充为零。")
        state.warnings.append("份额变化仅是资金行为代理信号，不等同于资金净流入或特定主体买卖。")
        state.final_report = self._report(profile, signal, docs, state.warnings)
        self.tools.repo.save_report(state.task_id, state.final_report)
        return state

    @staticmethod
    def _report(profile, signal, docs, warnings):
        return {"title": "ETF研究报告", "etf": profile.model_dump(mode="json") if profile else None, "as_of_date": signal.as_of_date.isoformat(), "indicators": signal.indicators, "signal": signal.signal, "evidence_strength": signal.evidence_strength, "public_documents": [d.model_dump(mode="json") for d in docs], "warnings": warnings, "disclaimer": "本报告仅供研究参考，不构成投资建议，也不提供自动交易或个性化投资建议。"}

