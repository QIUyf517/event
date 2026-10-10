"""Read the live DB into an isolated copy before one AI research run."""

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from app.agents.llm_research import ModelDrivenResearchAgent
from app.config import database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.tools.research_tools import ResearchTools


def main():
    source = database_path_for_mode("live")
    if not source.exists():
        raise FileNotFoundError(f"真实数据库不存在：{source}")
    with tempfile.TemporaryDirectory(prefix="etf-ai-live-snapshot-") as directory:
        copy_path = Path(directory) / "live-copy.sqlite3"
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(copy_path)) as destination:
                original.backup(destination)
        repo = SQLiteRepository(copy_path)
        try:
            state = ModelDrivenResearchAgent(ResearchTools(repo, "live")).run(
                "510300当前资金信号和已入库资料分别能说明什么？", "510300", date.today())
            report = state.final_report
            print(json.dumps({"answer_status": report["answer_status"],
                "direct_answer": report["direct_answer"],
                "source_dates": report["calculated_results"]["source_dates"],
                "document_hits": len(report["source_excerpts"]),
                "source_quotes": report["source_quotes"],
                "roles": [{"agent": x.agent, "status": x.status, "error": x.error}
                          for x in state.execution_trace],
                "warnings": report["warnings"], "wrote_live_database": False},
                ensure_ascii=False, indent=2))
        finally:
            repo.close()


if __name__ == "__main__":
    main()
