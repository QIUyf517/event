"""生成一份演示模式PDF，用于检查导出布局。"""

import argparse
import tempfile
from datetime import date
from pathlib import Path

from app.agents.single_agent import SingleResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.reporting.pdf_export import render_report_pdf
from app.tools.research_tools import ResearchTools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--etf", default="510300")
    parser.add_argument("--output", type=Path,
        default=Path("output/pdf/sample_etf_research_report.pdf"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="etf-pdf-sample-") as temp_dir:
        repo = SQLiteRepository(Path(temp_dir) / "demo.sqlite3")
        try:
            seed_demo(repo, args.as_of)
            state = SingleResearchAgent(ResearchTools(repo, "demo")).run(
                f"分析{args.etf}当前资金信号，并结合已入库公开资料说明证据和局限",
                args.etf, args.as_of)
            payload = render_report_pdf(state.final_report, state.task_id)
        finally:
            repo.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
