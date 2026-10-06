"""研究入口：协调、并行取证、核验报告。保留旧类名供现有入口调用。"""

from datetime import date, timedelta
from uuid import uuid4
from urllib.parse import urlparse

from app.agents.multi_agent import CoordinatorAgent, failed, run_workers, skipped, timed
from app.agents.question_router import QuestionPlan, SUPPORTED_EXAMPLES, answer_question
from app.agents.verifier import verify_generated_source_quotes
from app.analysis.signals import calculate_signal
from app.llm.client import OpenAICompatibleClient
from app.models.research import Evidence, ResearchReport, ResearchState
from app.tools.research_tools import ResearchTools

OFFICIAL_HOSTS = ("sse.com.cn", "szse.cn", "csrc.gov.cn", "csindex.com.cn")


def source_category(url):
    host = (urlparse(url).hostname or "").lower()
    if host == "example.com":
        return "demo"
    if any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_HOSTS):
        return "official"
    return "other"


class VerifierReporterAgent:
    def __init__(self, llm):
        self.llm = llm

    def run(self, state, plan, profile, signal, docs, market_failed, documents_failed, coordinator_failed=False):
        source_excerpts = []
        valid_docs = []
        for doc in docs:
            if doc.published_at > state.as_of_date or not doc.source_url:
                state.warnings.append(f"资料 {doc.chunk_id} 来源缺失或晚于查询日，已排除。")
                continue
            citation = f"D{len(valid_docs) + 1}"
            valid_docs.append(doc)
            state.evidence.append(Evidence(evidence_id=doc.chunk_id, evidence_type="document",
                title=doc.title, claim=doc.content, source_url=doc.source_url,
                source_date=doc.published_at, details={"citation": citation, "page": doc.page,
                "location": doc.location, "source_category": source_category(doc.source_url)},
                classification="source_excerpt"))
            source_excerpts.append({"citation": citation, "chunk_id": doc.chunk_id,
                "title": doc.title, "content": doc.content,
                "source_url": doc.source_url, "published_at": doc.published_at.isoformat(),
                "page": doc.page, "location": doc.location,
                "source_category": source_category(doc.source_url)})
        state.document_results = valid_docs
        if signal:
            state.market_results = [signal.model_dump(mode="json")]
            state.evidence.append(Evidence(evidence_id=f"data-{state.task_id}", evidence_type="data",
                title="确定性资金信号计算结果", claim="指标由程序根据已入库市场数据计算",
                details=signal.model_dump(mode="json"), classification="calculation"))
            if signal.missing_fields:
                state.warnings.append("部分指标缺少数据，系统未将缺失值填充为零。")
            if signal.source_dates.get("shares") and signal.source_dates.get("market") and signal.source_dates["shares"] < signal.source_dates["market"]:
                state.warnings.append(f"份额最近披露日为{signal.source_dates['shares']}，早于行情最近交易日{signal.source_dates['market']}；份额变化按可用披露记录计算。")
        if "shares" in plan.focus or "signal" in plan.focus:
            state.warnings.append("份额变化仅是资金行为代理信号，不等同于资金净流入或特定主体买卖。")
        if market_failed:
            state.warnings.append("数据分析 Agent 失败；不能生成资金信号。")
        if coordinator_failed:
            state.warnings.append("协调 Agent 失败；无法确定应执行哪些研究任务，已跳过取数和资料检索。")
        if documents_failed:
            state.warnings.append("公开资料 Agent 失败；保留可核验的行情计算结果。")
        elif plan.wants_documents and not valid_docs:
            state.warnings.append("未检索到足够的公开资料证据；系统不会补充未经核实的事件解释。")

        source_quotes = []
        if signal and valid_docs and plan.asks_cause and self.llm.configured:
            try:
                payload = {"signal": signal.model_dump(mode="json"), "documents": source_excerpts}
                generated = self.llm.summarize(state.question, payload)
                source_quotes, warnings = verify_generated_source_quotes(generated, source_excerpts,
                    {row["citation"] for row in source_excerpts})
                state.warnings.extend(warnings)
            except Exception as exc:
                state.warnings.append(f"原文摘录失败，已保留确定性报告: {exc}")

        unverified = []
        if plan.wants_documents and not valid_docs:
            unverified.append("公开资料不足，无法解释信号原因")
        elif plan.asks_cause:
            unverified.append("现有公开资料尚不能确认本次资金信号的成因")
        if signal and signal.missing_fields:
            unverified.append(f"缺少指标数据，无法确认完整资金信号: {', '.join(signal.missing_fields)}")
        if market_failed:
            unverified.append("行情分析失败，无法核实价格、份额和资金信号")
        if coordinator_failed:
            unverified.append("研究任务规划失败，本次没有经过核验的研究结论")

        if coordinator_failed:
            answer_status, direct_answer = "partial", "研究任务规划失败，本次未执行行情分析或资料检索，无法提供研究结论。"
        elif plan.unsupported_reason:
            answer_status, direct_answer = "unsupported", f"{plan.unsupported_reason} 目前支持：{SUPPORTED_EXAMPLES}。"
        elif market_failed:
            answer_status = "partial"
            direct_answer = "行情分析失败，无法提供可核验的价格、份额或资金信号。"
            if plan.wants_documents and valid_docs:
                first = valid_docs[0]
                direct_answer += f" 已入库资料命中{len(valid_docs)}个片段；其中《{first.title}》的入库发布日期为{first.published_at} [D1]。"
        else:
            answer_status, direct_answer = answer_question(plan, signal, valid_docs)
        report = ResearchReport(question=state.question, question_focus=list(plan.focus),
            answer_status=answer_status, direct_answer=direct_answer, data_mode=state.data_mode,
            etf=profile.model_dump(mode="json") if profile else {"etf_code": state.etf_codes[0], "name": state.etf_codes[0]},
            as_of_date=state.as_of_date,
            data_cutoff_date=min(signal.source_dates.values()) if signal and signal.source_dates else None,
            source_excerpts=source_excerpts, source_quotes=source_quotes,
            verified_facts=[], calculated_results=(signal or calculate_signal(
                state.etf_codes[0], state.as_of_date, [], [], state.data_mode)).model_dump(mode="json"),
            reasonable_inferences=[], unverified_information=unverified,
            evidence=source_excerpts, warnings=state.warnings, execution_trace=state.execution_trace,
            disclaimer="本报告仅供研究参考，不构成投资建议，也不提供自动交易或个性化投资建议。")
        state.final_report = report.model_dump(mode="json")
        return state


class SingleResearchAgent:
    """兼容第一阶段入口，内部已由四个独立角色执行。"""

    def __init__(self, tools: ResearchTools, llm: OpenAICompatibleClient | None = None):
        self.tools = tools
        self.llm = llm or OpenAICompatibleClient()

    def _rollback_failed_write(self):
        try:
            self.tools.repo.conn.rollback()
        except Exception:
            pass

    def _record(self, state, trace, payload, result, *, trace_input=None, trace_output=None, evidence_refs=None):
        trace.input_summary = payload if trace_input is None else trace_input
        trace.output_summary = result if trace_output is None else trace_output
        trace.evidence_refs = evidence_refs or []
        state.execution_trace.append(trace)
        try:
            self.tools.repo.save_tool_call(str(uuid4()), state.task_id, trace.agent,
                trace.status, payload, result)
        except Exception as exc:
            self._rollback_failed_write()
            state.warnings.append(f"{trace.agent}的执行日志未能入库: {exc}")

    def run(self, question: str, etf_code: str = "510300", as_of_date: date | None = None) -> ResearchState:
        question = question.strip()
        if not question:
            raise ValueError("请输入研究问题")
        as_of_date = as_of_date or date.today()
        plan, coordinator_trace = timed("协调 Agent", lambda: CoordinatorAgent().run(question, etf_code))
        if coordinator_trace.status == "success" and not isinstance(plan, QuestionPlan):
            coordinator_trace.status = "failed"
            coordinator_trace.error = "协调 Agent 返回无效研究计划"
        coordinator_failed = coordinator_trace.status == "failed"
        if coordinator_failed:
            plan = QuestionPlan(())
        steps = ["协调 Agent：解析研究问题"]
        if not coordinator_failed and not plan.unsupported_reason:
            steps.append("数据分析 Agent：计算信号")
            if plan.wants_documents:
                steps.append("公开资料 Agent：检索文档")
        steps.append("核验与报告 Agent：核对并生成报告")
        state = ResearchState(task_id=str(uuid4()), question=question, etf_codes=[etf_code],
            as_of_date=as_of_date, data_mode=self.tools.data_mode, plan=steps)
        self.tools.repo.save_task(state.task_id, question)
        coordinator_trace.summary = ("研究计划生成失败" if coordinator_failed else
            "问题超出当前支持范围" if plan.unsupported_reason else "研究计划已生成")
        self._record(state, coordinator_trace,
            {"question": question, "etf_code": etf_code, "as_of_date": as_of_date, "data_mode": self.tools.data_mode},
            {"plan": steps, "focus": list(plan.focus), "document_types": list(plan.document_types or ()),
             "unsupported_reason": plan.unsupported_reason, "error": coordinator_trace.error})

        profile = signal = None
        docs = []
        market_failed = documents_failed = False
        if coordinator_failed or plan.unsupported_reason:
            reason = "协调 Agent 失败，无法确定研究任务" if coordinator_failed else "问题超出当前支持范围"
            self._record(state, skipped("数据分析 Agent", reason), {}, {})
            self._record(state, skipped("公开资料 Agent", reason), {}, {})
        else:
            try:
                market_result, market_trace, document_result, document_trace = run_workers(
                    self.tools.repo.path, self.tools.data_mode, question, etf_code, as_of_date, plan)
            except Exception as exc:
                market_result, document_result = None, None
                market_trace = failed("数据分析 Agent", exc)
                document_trace = (failed("公开资料 Agent", exc) if plan.wants_documents else
                    skipped("公开资料 Agent", "问题无需检索公开资料"))
                state.warnings.append("并行任务调度失败；已停止使用未取得的研究结果。")
            market_failed = market_trace.status == "failed"
            documents_failed = document_trace.status == "failed"
            if market_result:
                profile, signal = market_result
                market_trace.summary = f"行情截至{signal.source_dates.get('market', '暂无')}；份额截至{signal.source_dates.get('shares', '暂无')}"
                try:
                    self.tools.repo.save_signal(signal)
                except Exception as exc:
                    self._rollback_failed_write()
                    state.warnings.append(f"信号结果未单独入库，报告继续生成: {exc}")
            if document_result is not None:
                docs = document_result
                if document_trace.status == "success":
                    document_trace.summary = f"命中{len(docs)}个资料片段"
            self._record(state, market_trace, {"etf_code": etf_code, "as_of_date": as_of_date},
                signal.model_dump(mode="json") if signal else {"error": market_trace.error},
                trace_input={"etf_code": etf_code, "as_of_date": as_of_date.isoformat(), "data_mode": self.tools.data_mode},
                trace_output=({"signal": signal.signal, "indicators": signal.indicators,
                    "source_dates": signal.model_dump(mode="json")["source_dates"],
                    "source_records": signal.source_records, "missing_fields": signal.missing_fields}
                    if signal else {"error": market_trace.error}),
                evidence_refs=[f"data-{state.task_id}"] if signal else [])
            self._record(state, document_trace, {"etf_code": etf_code, "question": question} if plan.wants_documents else {},
                [doc.model_dump(mode="json") for doc in docs] if not documents_failed else {"error": document_trace.error},
                trace_input=({"etf_code": etf_code, "question": question,
                    "query": f"{etf_code} {question}",
                    "date_range": [(as_of_date - timedelta(days=365)).isoformat(), as_of_date.isoformat()],
                    "document_types": list(plan.document_types or ()), "as_of_date": as_of_date.isoformat()}
                    if plan.wants_documents else {}),
                trace_output={"hit_count": len(docs), "hits": [{"chunk_id": doc.chunk_id,
                    "title": doc.title, "published_at": doc.published_at.isoformat(),
                    "source_url": doc.source_url, "source_category": source_category(doc.source_url)} for doc in docs],
                    "error": document_trace.error})

        reported, report_trace = timed("核验与报告 Agent", lambda: VerifierReporterAgent(self.llm).run(
            state, plan, profile, signal, docs, market_failed, documents_failed, coordinator_failed))
        if reported is None:
            state.evidence = []
            for earlier_trace in state.execution_trace:
                earlier_trace.evidence_refs = []
            state.warnings.append("核验与报告 Agent 失败；研究结论已停止输出，请查看执行轨迹。")
            state.final_report = ResearchReport(question=question, question_focus=list(plan.focus),
                answer_status="partial", direct_answer="报告核验失败，本次没有可发布的研究结论。",
                data_mode=state.data_mode, etf={"etf_code": etf_code, "name": etf_code},
                as_of_date=as_of_date, calculated_results=calculate_signal(
                    etf_code, as_of_date, [], [], state.data_mode).model_dump(mode="json"),
                unverified_information=["本次研究结论尚未通过核验"], warnings=state.warnings,
                disclaimer="本报告仅供研究参考，不构成投资建议，也不提供自动交易或个性化投资建议。").model_dump(mode="json")
        else:
            report_trace.summary = "已核对日期、数字来源和引用，生成报告"
            source_rows = state.final_report.get("source_excerpts", [])
            if len(state.execution_trace) > 2:
                state.execution_trace[2].evidence_refs = [row["citation"] for row in source_rows]
                state.execution_trace[2].output_summary["retained_citations"] = [row["citation"] for row in source_rows]
        report_refs = ([f"data-{state.task_id}"] if reported is not None and signal else []) + [
            row["citation"] for row in state.final_report.get("source_excerpts", [])]
        self._record(state, report_trace, {}, {"answer_status": state.final_report["answer_status"]},
            trace_input={"market_available": signal is not None,
                "document_hits": len(docs), "source_excerpts": len(state.final_report.get("source_excerpts", [])),
                "as_of_date": as_of_date.isoformat()},
            trace_output={"answer_status": state.final_report["answer_status"],
                "verified_fact_count": len(state.final_report.get("verified_facts", [])),
                "source_excerpt_count": len(state.final_report.get("source_excerpts", [])),
                "source_quote_count": len(state.final_report.get("source_quotes", [])),
                "warnings": list(state.warnings)}, evidence_refs=report_refs)
        state.final_report["execution_trace"] = [item.model_dump(mode="json") for item in state.execution_trace]
        try:
            self.tools.repo.save_evidence(state.task_id, state.evidence)
        except Exception as exc:
            self._rollback_failed_write()
            state.warnings.append(f"证据明细未能单独入库，报告仍保留证据内容: {exc}")
        state.final_report["warnings"] = state.warnings
        self.tools.repo.save_report(state.task_id, state.final_report)
        task_status = "completed_with_warnings" if any(
            item.status == "failed" for item in state.execution_trace) or any(
            "未能入库" in warning for warning in state.warnings) else "completed"
        self.tools.repo.update_task_status(state.task_id, task_status)
        return state
