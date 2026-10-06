from datetime import date
import json
from pathlib import Path

import pytest

from app.agents import multi_agent
from app.agents.single_agent import SingleResearchAgent
from app.agents import single_agent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


AS_OF = date(2026, 9, 28)
QUESTION = "510300当前资金信号和定期报告资料"


def build_agent(tmp_path):
    repo = SQLiteRepository(tmp_path / "phase2.sqlite3")
    seed_demo(repo, AS_OF)
    return repo, SingleResearchAgent(ResearchTools(repo, "demo"))


def test_parallel_roles_have_persisted_trace_and_independent_connections(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)
    opened = []
    original = SQLiteRepository.open_readonly.__func__

    def track(cls, path):
        connection = original(cls, path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(SQLiteRepository, "open_readonly", classmethod(track))
    state = agent.run(QUESTION, "510300", AS_OF)
    assert len(opened) == 2 and opened[0] is not opened[1]
    assert [step.agent for step in state.execution_trace] == [
        "协调 Agent", "数据分析 Agent", "公开资料 Agent", "核验与报告 Agent"]
    assert all(step.status == "success" for step in state.execution_trace)
    assert state.final_report["execution_trace"][-1]["status"] == "success"
    assert state.final_report["execution_trace"][0]["input_summary"]["question"] == QUESTION
    assert state.final_report["execution_trace"][1]["output_summary"]["source_dates"]["market"] == "2026-09-28"
    assert state.final_report["execution_trace"][2]["evidence_refs"] == ["D1"]
    assert state.final_report["execution_trace"][3]["evidence_refs"]
    assert repo.conn.execute("SELECT COUNT(*) FROM tool_calls WHERE task_id=?", (state.task_id,)).fetchone()[0] == 4
    repo.close()


def test_document_failure_keeps_market_answer(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("document source unavailable")

    monkeypatch.setattr(multi_agent.DocumentResearchAgent, "run", fail)
    report = agent.run(QUESTION, "510300", AS_OF).final_report
    assert report["calculated_results"]["signal"] != "insufficient_data"
    assert report["execution_trace"][2]["status"] == "failed"
    assert report["source_excerpts"] == []
    assert any("公开资料 Agent 失败" in warning for warning in report["warnings"])
    repo.close()


def test_market_failure_never_claims_signal(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("market source unavailable")

    monkeypatch.setattr(multi_agent.MarketAnalystAgent, "run", fail)
    report = agent.run(QUESTION, "510300", AS_OF).final_report
    assert report["answer_status"] == "partial"
    assert "行情分析失败" in report["direct_answer"]
    assert report["calculated_results"]["signal"] == "insufficient_data"
    assert report["source_excerpts"]
    assert report["execution_trace"][1]["status"] == "failed"
    repo.close()


def test_unsupported_question_skips_workers(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(multi_agent, "run_workers", fail)
    report = agent.run("预测510300明天涨跌", "510300", AS_OF).final_report
    assert report["answer_status"] == "unsupported"
    assert [row["status"] for row in report["execution_trace"]] == ["success", "skipped", "skipped", "success"]
    repo.close()


def test_reporter_failure_persists_safe_degraded_report(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("verification unavailable")

    monkeypatch.setattr(single_agent.VerifierReporterAgent, "run", fail)
    report = agent.run(QUESTION, "510300", AS_OF).final_report
    assert report["direct_answer"] == "报告核验失败，本次没有可发布的研究结论。"
    assert report["calculated_results"]["signal"] == "insufficient_data"
    assert report["execution_trace"][-1]["status"] == "failed"
    assert repo.conn.execute("SELECT status FROM research_tasks ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "completed_with_warnings"
    repo.close()


def test_coordinator_failure_skips_workers_and_persists_degraded_report(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(multi_agent.CoordinatorAgent, "run", fail)
    monkeypatch.setattr(single_agent, "run_workers", lambda *args: pytest.fail("workers must be skipped"))
    state = agent.run(QUESTION, "510300", AS_OF)
    report = state.final_report
    assert report["answer_status"] == "partial"
    assert "研究任务规划失败" in report["direct_answer"]
    assert report["calculated_results"]["signal"] == "insufficient_data"
    assert report["source_excerpts"] == []
    assert [step["status"] for step in report["execution_trace"]] == ["failed", "skipped", "skipped", "success"]
    saved = repo.conn.execute("SELECT payload FROM research_reports WHERE task_id=?", (state.task_id,)).fetchone()
    assert json.loads(saved[0])["direct_answer"] == report["direct_answer"]
    assert repo.conn.execute("SELECT status FROM research_tasks WHERE task_id=?", (state.task_id,)).fetchone()[0] == "completed_with_warnings"
    repo.close()


def test_invalid_coordinator_result_is_degraded(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)
    monkeypatch.setattr(multi_agent.CoordinatorAgent, "run", lambda *args: None)
    report = agent.run(QUESTION, "510300", AS_OF).final_report
    assert report["answer_status"] == "partial"
    assert report["execution_trace"][0]["status"] == "failed"
    assert report["execution_trace"][0]["error"] == "协调 Agent 返回无效研究计划"
    repo.close()


def test_parallel_dispatch_failure_persists_safe_report(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(single_agent, "run_workers", fail)
    report = agent.run(QUESTION, "510300", AS_OF).final_report
    assert report["answer_status"] == "partial"
    assert report["source_excerpts"] == []
    assert report["calculated_results"]["signal"] == "insufficient_data"
    assert [step["status"] for step in report["execution_trace"]] == ["success", "failed", "failed", "success"]
    assert any("并行任务调度失败" in warning for warning in report["warnings"])
    repo.close()


def test_blank_question_is_input_error_not_degraded_research(tmp_path):
    repo, agent = build_agent(tmp_path)
    with pytest.raises(ValueError, match="请输入研究问题"):
        agent.run("  ", "510300", AS_OF)
    assert repo.conn.execute("SELECT COUNT(*) FROM research_tasks").fetchone()[0] == 0
    repo.close()


def test_streamlit_shows_coordinator_failure_without_crashing(tmp_path, monkeypatch):
    import app.config as config
    from streamlit.testing.v1 import AppTest

    def fail(*args, **kwargs):
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "ui.sqlite3")
    monkeypatch.setattr(config, "DATA_MODE", "demo")
    monkeypatch.setattr(multi_agent.CoordinatorAgent, "run", fail)
    page_path = Path(__file__).parents[1] / "app" / "frontend" / "streamlit_app.py"
    page = AppTest.from_file(str(page_path)).run()
    assert not page.exception
    next(button for button in page.button if button.label == "生成研究简报").click().run()
    assert not page.exception
    assert any("协调角色 · 失败" in item.label for item in page.expander)


def test_audit_detail_write_failure_keeps_report_and_marks_warning(tmp_path, monkeypatch):
    repo, agent = build_agent(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("audit detail unavailable")

    monkeypatch.setattr(repo, "save_tool_call", fail)
    monkeypatch.setattr(repo, "save_evidence", fail)
    state = agent.run(QUESTION, "510300", AS_OF)
    assert state.final_report["answer_status"] == "answered"
    assert any("执行日志未能入库" in warning for warning in state.final_report["warnings"])
    assert any("证据明细未能单独入库" in warning for warning in state.final_report["warnings"])
    saved = repo.conn.execute("SELECT payload FROM research_reports WHERE task_id=?", (state.task_id,)).fetchone()
    assert json.loads(saved[0])["warnings"] == state.final_report["warnings"]
    assert repo.conn.execute("SELECT status FROM research_tasks WHERE task_id=?", (state.task_id,)).fetchone()[0] == "completed_with_warnings"
    repo.close()
