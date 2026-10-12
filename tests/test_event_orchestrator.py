import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents.event_orchestrator import EventResearchOrchestrator
from app.api.main import create_app
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.evaluation.event_suites import audit_event_suites
from app.rag.ingest import build_document


START, END = date(2026, 8, 14), date(2026, 9, 13)


class FakeLLM:
    configured = True

    def __init__(self, plan, total_tokens=100):
        self.plan, self.total_tokens = plan, total_tokens

    def chat(self, messages, **kwargs):
        assert kwargs["json_output"] is True
        return {"message": {"content": json.dumps(self.plan, ensure_ascii=False)},
                "usage": {"total_tokens": self.total_tokens}, "model": "fake-model"}


def step(step_id, tool, query="营业收入", depends_on=None, on_failure="continue"):
    return {"step_id": step_id, "objective": "核对原文", "tool": tool, "query": query,
            "depends_on": depends_on or [], "completion_condition": "至少一条原文片段",
            "failure_fallback": "保留已有证据并标记资料不足", "selection_reason": "问题需要此类来源",
            "min_results": 1, "on_failure": on_failure}


@pytest.fixture()
def corpus(tmp_path):
    path = tmp_path / "events.sqlite3"
    repo = SQLiteRepository(path)
    seed_event_research_subjects(repo)
    for kind, day in (("financial", date(2026, 8, 15)),
                      ("news", date(2026, 8, 20))):
        text = f"贵州茅台{kind}资料披露营业收入，日期{day}。"
        document, chunks = build_document(
            f"<article><p>{text}</p></article>".encode(), "test.html",
            f"贵州茅台{kind}", "测试来源", day,
            f"https://www.stcn.com/article/{kind}.html", kind,
            subject_id="600519", source_id="stcn_news")
        repo.store_event_document(document, chunks)
    repo.close()
    return path


@pytest.mark.parametrize("question,steps", [
    ("梳理事件", [step("announcement", "search_announcements"),
               step("news", "search_news")]),
    ("解读财报", [step("financial", "search_financial_materials")]),
    ("核对财务变化与报道", [step("financial", "search_financial_materials"),
                       step("followup", "search_news", depends_on=["financial"])]),
    ("追问可能原因", [step("news", "search_news"),
                step("financial", "search_financial_materials")]),
])
def test_model_controls_distinct_event_tool_graphs(corpus, question, steps):
    plan = {"steps": steps, "stop_condition": "取证节点完成后停止"}
    result = EventResearchOrchestrator(corpus, FakeLLM(plan)).run(
        question, "600519", END, START)
    assert [(item.tool, item.depends_on) for item in result.plan.steps] == [
        (item["tool"], item["depends_on"]) for item in steps]
    assert result.status == "evidence_collected"
    assert result.evidence
    assert all(START <= item.published_at.date() <= END for item in result.evidence)
    assert result.total_tokens == 100
    assert all(item.selection_reason for item in result.plan.steps)


def test_failure_preserves_other_evidence_and_marks_partial(corpus):
    plan = {"steps": [step("news", "search_news"),
                      step("missing", "search_financial_materials",
                           query="不存在的关键词", on_failure="stop"),
                      step("dependent", "search_news", depends_on=["missing"])],
            "stop_condition": "节点结束后停止"}
    result = EventResearchOrchestrator(corpus, FakeLLM(plan)).run(
        "核对资料", "600519", END, START)
    assert result.status == "partial"
    assert result.evidence
    assert {item.status for item in result.execution_trace} == {"success", "failed", "skipped"}
    assert next(item for item in result.execution_trace if item.step_id == "dependent").tool_calls == []
    assert "失败" in result.stop_reason


@pytest.mark.parametrize("mutate", [
    lambda plan: plan["steps"][0].update(tool="delete_database"),
    lambda plan: plan.update(subject_code="510300"),
    lambda plan: plan.update(as_of_date="2026-10-12"),
    lambda plan: plan["steps"].append(step("cycle", "search_news", depends_on=["cycle"])),
    lambda plan: (plan["steps"][0].update(depends_on=["second"]),
                  plan["steps"].append(step("second", "search_news", depends_on=["first"]))),
])
def test_unsafe_model_plans_are_rejected_before_tool_execution(corpus, mutate):
    plan = {"steps": [step("first", "search_news")], "stop_condition": "停止"}
    mutate(plan)
    with pytest.raises(ValueError):
        EventResearchOrchestrator(corpus, FakeLLM(plan)).run("查看资料", "600519", END, START)


def test_scope_budget_and_model_switch_fail_closed(corpus):
    plan = {"steps": [step("first", "search_news")], "stop_condition": "停止"}
    agent = EventResearchOrchestrator(corpus, FakeLLM(plan))
    with pytest.raises(ValueError, match="研究对象"):
        agent.run("查看资料", "510300", END, START)
    with pytest.raises(ValueError, match="日期"):
        agent.run("查看资料", "600519", date.today() + timedelta(days=1), START)
    with pytest.raises(ValueError, match="Token"):
        EventResearchOrchestrator(corpus, FakeLLM(plan, total_tokens=3001)).run(
            "查看资料", "600519", END, START)
    with pytest.raises(RuntimeError, match="需要已配置的大模型"):
        EventResearchOrchestrator(corpus, SimpleNamespace(configured=False)).run(
            "查看资料", "600519", END, START)


def test_saved_company_market_is_dated_and_source_backed(corpus):
    repo = SQLiteRepository(corpus)
    try:
        repo.upsert_company_market([{"subject_id": "600519", "trade_date": "2026-08-19",
            "close": 1500.0, "volume": 120.0, "turnover": 180000.0,
            "source": "fixture_exchange", "source_url": "https://example.org/market/600519",
            "fetched_at": "2026-10-12T00:00:00+00:00"}])
    finally:
        repo.close()
    plan = {"steps": [step("market", "get_company_market_data", query="价格成交")],
            "stop_condition": "行情取证完成"}
    result = EventResearchOrchestrator(corpus, FakeLLM(plan)).run("查看行情", "600519", END, START)
    assert result.status == "evidence_collected"
    assert len(result.evidence) == 1
    assert result.evidence[0].source_type == "market"
    assert result.evidence[0].published_at.date() == date(2026, 8, 19)
    assert "1500.0" in result.evidence[0].excerpt


def test_fee_preflight_rejects_request_before_model_call(corpus, monkeypatch):
    monkeypatch.setenv("EVENT_LLM_MAX_USD_PER_RUN", "0.000001")
    class PricedLLM(FakeLLM):
        model = "deepseek-v4-flash"
        base_url = "https://api.deepseek.com"

        def chat(self, messages, **kwargs):
            raise AssertionError("超预算时不应调用模型")

    plan = {"steps": [step("news", "search_news")], "stop_condition": "完成"}
    with pytest.raises(ValueError, match="预估费用"):
        EventResearchOrchestrator(corpus, PricedLLM(plan)).run("查看资料", "600519", END, START)


def test_api_plan_run_needs_key_and_keeps_real_and_synthetic_suites_separate(corpus, tmp_path, monkeypatch):
    import app.api.main as api_module

    def fake_run(self, question, subject_code, as_of_date, window_start):
        return SimpleNamespace(model_dump=lambda **_: {"subject_code": subject_code,
            "question": question, "status": "evidence_collected"})

    monkeypatch.setattr(api_module.EventResearchOrchestrator, "run", fake_run)
    with TestClient(create_app(event_database_path=corpus, write_key="test-secret",
                               settings_path=tmp_path / "settings.sqlite3")) as client:
        payload = {"question": "查看资料", "subject_code": "600519",
                   "window_start": START.isoformat(), "as_of_date": END.isoformat()}
        assert client.post("/api/v1/event-research/plan-run", json=payload).status_code == 401
        response = client.post("/api/v1/event-research/plan-run", json=payload,
                               headers={"X-API-Key": "test-secret"})
        assert response.status_code == 200
        assert response.json()["run"]["status"] == "evidence_collected"

    audit = audit_event_suites(corpus)
    assert audit["synthetic_benchmark"]["case_count"] == 20
    assert audit["synthetic_benchmark"]["status"] == "annotations_only_not_executed"
    assert audit["real_snapshot"]["case_count"] == 3
    assert audit["real_snapshot"]["status"] == "missing_sources"
    assert audit["combined_answer_score"] is None
