"""第二阶段的角色和并行研究执行器。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from time import perf_counter

from app.agents.question_router import plan_question
from app.analysis.signals import calculate_signal
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import AgentTrace
from app.tools.research_tools import ResearchTools


def timed(agent, fn):
    started = datetime.now(timezone.utc)
    clock = perf_counter()
    try:
        value = fn()
        status, error = "success", None
    except Exception as exc:
        value = None
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    finished = datetime.now(timezone.utc)
    return value, AgentTrace(agent=agent, status=status, started_at=started,
        finished_at=finished, duration_ms=round((perf_counter() - clock) * 1000),
        summary="任务完成" if status == "success" else "任务失败", error=error)


def skipped(agent, reason):
    now = datetime.now(timezone.utc)
    return AgentTrace(agent=agent, status="skipped", started_at=now,
        finished_at=now, duration_ms=0, summary=reason)


def failed(agent, error):
    now = datetime.now(timezone.utc)
    return AgentTrace(agent=agent, status="failed", started_at=now,
        finished_at=now, duration_ms=0, summary="任务失败", error=f"{type(error).__name__}: {error}")


class CoordinatorAgent:
    def run(self, question, etf_code):
        return plan_question(question, etf_code)


class MarketAnalystAgent:
    def __init__(self, path, data_mode):
        self.path, self.data_mode = path, data_mode

    def run(self, etf_code, as_of_date):
        repo = SQLiteRepository.open_readonly(self.path)
        try:
            profile = ResearchTools(repo, self.data_mode).get_etf_profile(etf_code)
            signal = calculate_signal(etf_code, as_of_date, repo.market(etf_code, as_of_date),
                                      repo.shares(etf_code, as_of_date), self.data_mode)
            return profile, signal
        finally:
            repo.close()


class DocumentResearchAgent:
    def __init__(self, path, data_mode):
        self.path, self.data_mode = path, data_mode

    def run(self, question, etf_code, as_of_date, document_types):
        repo = SQLiteRepository.open_readonly(self.path)
        try:
            return ResearchTools(repo, self.data_mode).search_public_documents(
                f"{etf_code} {question}", etf_code,
                (as_of_date - timedelta(days=365), as_of_date),
                list(document_types) if document_types else None)
        finally:
            repo.close()


def run_workers(path, data_mode, question, etf_code, as_of_date, question_plan):
    """每个工作线程独占只读连接；返回值由主线程统一写入。"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        market = pool.submit(timed, "数据分析 Agent", lambda: MarketAnalystAgent(path, data_mode).run(etf_code, as_of_date))
        documents = (pool.submit(timed, "公开资料 Agent", lambda: DocumentResearchAgent(path, data_mode).run(
            question, etf_code, as_of_date, question_plan.document_types)) if question_plan.wants_documents else None)
        market_result, market_trace = market.result()
        if documents:
            document_result, document_trace = documents.result()
        else:
            document_result, document_trace = [], skipped("公开资料 Agent", "问题无需检索公开资料")
    return market_result, market_trace, document_result, document_trace
