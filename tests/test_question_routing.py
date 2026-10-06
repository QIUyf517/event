from datetime import date

import pytest

from app.agents.question_router import plan_question
from app.agents.single_agent import SingleResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


def test_blank_question_is_rejected_before_running():
    with pytest.raises(ValueError, match="请输入研究问题"):
        plan_question("  ", "510300")


def test_question_changes_answer_and_document_tool_choice(tmp_path):
    repo = SQLiteRepository(tmp_path / "questions.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    agent = SingleResearchAgent(ResearchTools(repo, "demo"))

    shares = agent.run("510300近5个可用披露日份额变化是多少", "510300", date(2026, 9, 28))
    assert shares.final_report["answer_status"] == "answered"
    assert shares.final_report["question_focus"] == ["shares"]
    assert "最近5个可用披露日变化" in shares.final_report["direct_answer"]
    assert shares.document_results == []
    assert repo.conn.execute("SELECT COUNT(*) FROM tool_calls WHERE task_id=? AND tool_name='search_public_documents'", (shares.task_id,)).fetchone()[0] == 0

    documents = agent.run("510300定期报告发布日期是什么", "510300", date(2026, 9, 28))
    assert documents.final_report["question_focus"] == ["documents"]
    assert documents.document_results
    assert "[D1]" in documents.final_report["direct_answer"]
    assert documents.final_report["direct_answer"] != shares.final_report["direct_answer"]

    missing_announcement = agent.run("510300公告发布日期是什么", "510300", date(2026, 9, 28))
    assert missing_announcement.final_report["answer_status"] == "partial"
    assert missing_announcement.document_results == []
    repo.close()


def test_unsupported_window_and_other_etf_are_not_answered_as_five_day_change(tmp_path):
    repo = SQLiteRepository(tmp_path / "questions.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    agent = SingleResearchAgent(ResearchTools(repo, "demo"))
    window = agent.run("510300近20个交易日份额变化是多少", "510300", date(2026, 9, 28)).final_report
    assert window["answer_status"] == "unsupported"
    assert "不能替代指定交易日区间" in window["direct_answer"]
    assert "最近5个可用披露日变化" not in window["direct_answer"]
    other = agent.run("510050当前资金信号如何", "510300", date(2026, 9, 28)).final_report
    assert other["answer_status"] == "unsupported"
    assert "其他ETF" in other["direct_answer"]
    repo.close()
