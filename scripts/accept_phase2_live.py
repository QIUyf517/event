"""在实时数据库的临时快照上验收三只 ETF 的第二阶段流程。"""

import argparse
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from app.agents.single_agent import SingleResearchAgent
from app.config import ALLOWED_ETFS, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.tools.research_tools import ResearchTools


def snapshot_live_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def check_research(repo: SQLiteRepository, etf_code: str, as_of_date: date) -> dict:
    question = f"分析{etf_code}当前资金信号，并结合已入库公开资料说明证据和局限"
    state = SingleResearchAgent(ResearchTools(repo, "live")).run(question, etf_code, as_of_date)
    report = state.final_report
    signal = report["calculated_results"]
    traces = report["execution_trace"]
    assert report["data_mode"] == "live"
    assert report["etf"]["etf_code"] == etf_code
    assert report["answer_status"] == "answered", (etf_code, report["direct_answer"])
    assert signal["signal"] != "insufficient_data", (etf_code, signal["missing_fields"])
    assert all(value is not None for value in signal["indicators"].values())
    assert len(traces) == 4 and all(step["status"] == "success" for step in traces), traces
    assert len(report["source_excerpts"]) >= 1
    assert report["verified_facts"] == []
    assert "[D1]" in report["direct_answer"]
    assert all(item["source_category"] == "official" for item in report["source_excerpts"])
    assert all(item["published_at"] <= as_of_date.isoformat() and item["source_url"] for item in report["source_excerpts"])
    assert traces[2]["evidence_refs"] == [item["citation"] for item in report["source_excerpts"]]
    assert traces[1]["input_summary"]["etf_code"] == etf_code
    assert traces[3]["output_summary"]["source_excerpt_count"] == len(report["source_excerpts"])
    assert all(value <= as_of_date.isoformat() for value in signal["source_dates"].values())

    raw_dates = {}
    raw_counts = {}
    for label, table in (("market", "etf_daily"), ("shares", "etf_shares")):
        count, latest = repo.conn.execute(
            f"SELECT COUNT(*), MAX(trade_date) FROM {table} WHERE etf_code=? AND trade_date<=?",
            (etf_code, as_of_date.isoformat()),
        ).fetchone()
        raw_counts[label] = count
        raw_dates[label] = latest
        assert count > 0 and signal["source_dates"][label] == latest
    saved = repo.conn.execute("SELECT payload FROM research_reports WHERE task_id=?", (state.task_id,)).fetchone()
    assert saved is not None and json.loads(saved[0])["execution_trace"] == traces
    calls = repo.conn.execute("SELECT status FROM tool_calls WHERE task_id=?", (state.task_id,)).fetchall()
    assert len(calls) == 4 and all(row[0] == "success" for row in calls)
    task = repo.conn.execute("SELECT status FROM research_tasks WHERE task_id=?", (state.task_id,)).fetchone()
    assert task[0] == "completed"
    return {
        "etf_code": etf_code,
        "input_rows": raw_counts,
        "source_dates": raw_dates,
        "signal": signal["signal"],
        "indicator_count": len(signal["indicators"]),
        "official_document_count": len(report["source_excerpts"]),
        "agent_statuses": [step["status"] for step in traces],
        "saved_tool_calls": len(calls),
        "task_status": task[0],
    }


def check_streamlit(snapshot_path: Path) -> dict:
    import app.config as config
    from streamlit.testing.v1 import AppTest

    config.DATABASE_PATH = snapshot_path.with_name("etf_research.sqlite3")
    config.DATA_MODE = "live"
    results = {}
    for etf_code in ALLOWED_ETFS:
        page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app" / "frontend" / "streamlit_app.py")).run()
        assert not page.exception, [item.message for item in page.exception]
        page.selectbox[0].select(etf_code).run()
        assert not page.exception, [item.message for item in page.exception]
        next(button for button in page.button if button.label == "生成研究简报").click().run()
        assert not page.exception, [item.message for item in page.exception]
        markdown = [item.value for item in page.markdown]
        assert any("研究过程" in value for value in markdown)
        assert sum("角色 · " in item.label for item in page.expander) == 4
        assert len(page.json) >= 8  # 四个 Agent 各有输入、输出摘要。
        assert any("针对问题的回答" in value for value in markdown)
        results[etf_code] = "passed"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    source_path = database_path_for_mode("live")
    if not source_path.is_file():
        parser.error(f"实时数据库不存在: {source_path}")
    with tempfile.TemporaryDirectory(prefix="etf-phase2-accept-") as temp_dir:
        snapshot_path = Path(temp_dir) / "etf_research.live.sqlite3"
        snapshot_live_database(source_path, snapshot_path)
        repo = SQLiteRepository(snapshot_path)
        try:
            results = [check_research(repo, code, args.as_of) for code in ALLOWED_ETFS]
        finally:
            repo.close()
        ui_results = check_streamlit(snapshot_path)
    print(json.dumps({"as_of_date": args.as_of.isoformat(),
                      "database_access": "original_read_only; temporary_snapshot_read_write",
                      "research": results, "streamlit": ui_results}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
