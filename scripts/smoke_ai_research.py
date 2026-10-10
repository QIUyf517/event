"""One real model run against an isolated synthetic-data database."""

import json
import tempfile
from datetime import date
from pathlib import Path

from app.agents.llm_research import ModelDrivenResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


def main():
    as_of = date(2026, 9, 30)
    with tempfile.TemporaryDirectory(prefix="etf-ai-smoke-") as directory:
        repo = SQLiteRepository(Path(directory) / "demo.sqlite3")
        try:
            seed_demo(repo, as_of)
            state = ModelDrivenResearchAgent(ResearchTools(repo, "demo")).run(
                "510300当前资金信号和已入库公告分别能说明什么？", "510300", as_of)
            report = state.final_report
            print(json.dumps({
                "method": report["research_method"],
                "answer_status": report["answer_status"],
                "direct_answer": report["direct_answer"],
                "source_dates": report["calculated_results"]["source_dates"],
                "document_hits": len(report["source_excerpts"]),
                "roles": [{"agent": row.agent, "status": row.status,
                           "duration_ms": row.duration_ms, "error": row.error}
                          for row in state.execution_trace],
                "warnings": report["warnings"],
            }, ensure_ascii=False, indent=2))
        finally:
            repo.close()


if __name__ == "__main__":
    main()
