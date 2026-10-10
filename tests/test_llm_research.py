import json
from datetime import date

from app.agents.llm_research import ModelDrivenResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


AS_OF = date(2026, 9, 28)


def answer(content=None, calls=None):
    return {"message": {"role": "assistant", "content": content, "tool_calls": calls or []},
            "usage": {"total_tokens": 9}, "model": "fake", "finish_reason": "tool_calls" if calls else "stop"}


def tool(name, args, ident="call_1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModel:
    configured = True

    def __init__(self, replies):
        self.replies = iter(replies)

    def chat(self, messages, **kwargs):
        return next(self.replies)


def build(tmp_path, replies):
    repo = SQLiteRepository(tmp_path / "research.sqlite3")
    seed_demo(repo, AS_OF)
    return repo, ModelDrivenResearchAgent(ResearchTools(repo, "demo"), FakeModel(replies))


def test_model_directs_tools_but_program_controls_numbers_and_quotes(tmp_path):
    replies = [
        answer('{"focus":["signal","documents"],"document_query":"510300 做市","reason":"核对数据和资料"}'),
        answer(calls=[tool("get_signal", {})]),
        answer(calls=[tool("search_documents", {"query": "510300"})]), answer("已读取资料"),
        answer('{"supported":true,"source_quotes":["不存在的原文 [D1]"],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    state = agent.run("510300当前资金信号和已入库资料", "510300", AS_OF)
    report = state.final_report
    assert report["research_method"] == "ai_collaboration"
    assert [x.agent for x in state.execution_trace] == [
        "模型规划 Agent", "模型数据分析 Agent", "模型公开资料 Agent", "程序核验与报告", "模型核验 Agent"]
    assert report["calculated_results"]["signal"] != "insufficient_data"
    assert report["source_excerpts"]
    assert report["source_quotes"] == []
    assert any("已删除" in warning for warning in report["warnings"])
    assert repo.conn.execute("SELECT COUNT(*) FROM tool_calls WHERE task_id=?", (state.task_id,)).fetchone()[0] == 7
    assert repo.get_report(state.task_id)["report"]["research_method"] == "ai_collaboration"
    repo.close()


def test_model_cannot_use_unapproved_tool_or_publish_unreviewed_answer(tmp_path):
    replies = [
        answer('{"focus":["signal"],"document_query":"","reason":"指标"}'),
        answer(calls=[tool("delete_database", {})]),
        answer('{"supported":true,"source_quotes":[],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300当前资金信号", "510300", AS_OF).final_report
    assert report["answer_status"] == "partial"
    assert "行情分析失败" in report["direct_answer"]
    assert report["calculated_results"]["signal"] == "insufficient_data"
    assert report["execution_trace"][1]["status"] == "failed"
    assert repo.conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0] > 0
    repo.close()


def test_review_rejection_hides_answer_but_keeps_auditable_data(tmp_path):
    replies = [
        answer('{"focus":["signal","documents"],"document_query":"510300","reason":"指标和资料"}'),
        answer(calls=[tool("get_signal", {})]),
        answer(calls=[tool("search_documents", {"query": "510300"})]), answer("已读取资料"),
        answer('{"supported":false,"source_quotes":[],"concerns":["证据不足"]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300当前资金信号和资料", "510300", AS_OF).final_report
    assert report["answer_status"] == "partial"
    assert "依据不足" in report["direct_answer"]
    assert report["calculated_results"]["source_dates"]["market"] == "2026-09-28"
    assert "证据不足" in report["warnings"]
    repo.close()


def test_planner_cannot_expand_question_to_unrequested_metrics(tmp_path):
    replies = [
        answer('{"focus":["signal","price"],"document_query":"","reason":"综合观察"}'),
        answer(calls=[tool("get_signal", {})]),
        answer('{"supported":true,"source_quotes":[],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300当前资金信号", "510300", AS_OF).final_report
    assert report["question_focus"] == ["signal"]
    assert "收盘价为" not in report["direct_answer"]
    assert report["execution_trace"][0]["output_summary"]["discarded_focus"] == ["price"]
    repo.close()


def test_valid_literal_quote_is_added_to_document_answer(tmp_path):
    replies = [
        answer('{"focus":["documents"],"document_query":"510300","reason":"核对资料"}'),
        answer(calls=[tool("search_documents", {"query": "510300"})]), answer("已读取资料"),
        answer('{"supported":true,"source_quotes":["本文件为合成演示资料，不代表真实公告 [D1]"],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300已入库资料说明什么？", "510300", AS_OF).final_report
    assert report["source_quotes"] == ["“本文件为合成演示资料，不代表真实公告” [D1]"]
    assert "资料原文摘录" in report["direct_answer"]
    assert report["calculated_results"]["signal"] != "insufficient_data"
    repo.close()


def test_empty_model_search_uses_labeled_background_fallback(tmp_path):
    replies = [
        answer('{"focus":["documents"],"document_query":"无关词","reason":"找资料"}'),
        answer(calls=[tool("search_documents", {"query": "完全无关的检索词"})]),
        answer("没有检索结果"),
        answer('{"supported":true,"source_quotes":[],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300已入库资料说明什么？", "510300", AS_OF).final_report
    assert report["source_excerpts"]
    assert any("背景资料" in warning for warning in report["warnings"])
    calls = report["execution_trace"][2]["output_summary"]["tool_calls"]
    assert calls[-1]["fallback"] is True
    repo.close()


def test_planner_cannot_drop_an_explicitly_requested_document_topic(tmp_path):
    replies = [
        answer('{"focus":["signal"],"document_query":"510300","reason":"看指标"}'),
        answer(calls=[tool("get_signal", {})]),
        answer(calls=[tool("search_documents", {"query": "510300"})]), answer("已读取资料"),
        answer('{"supported":true,"source_quotes":[],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)
    report = agent.run("510300当前资金信号和已入库资料", "510300", AS_OF).final_report
    assert report["question_focus"] == ["signal", "documents"]
    assert report["source_excerpts"]
    assert report["execution_trace"][0]["output_summary"]["restored_focus"] == ["documents"]
    repo.close()


def test_requested_document_type_is_kept_during_search_and_fallback(tmp_path):
    replies = [
        answer('{"focus":["documents"],"document_query":"510500 政策","reason":"核对政策资料"}'),
        answer(calls=[tool("search_documents", {"query": "510500 政策"})]),
        answer("没有检索结果"),
        answer('{"supported":true,"source_quotes":[],"concerns":[]}'),
    ]
    repo, agent = build(tmp_path, replies)

    report = agent.run("510500已入库的政策资料说明了什么？", "510500", AS_OF).final_report

    assert report["answer_status"] == "partial"
    assert report["source_excerpts"] == []
    assert "没有找到" in report["direct_answer"]
    assert report["execution_trace"][0]["output_summary"]["document_types"] == ["policy"]
    assert report["execution_trace"][2]["output_summary"]["tool_calls"][-1]["hits"] == []
    repo.close()
