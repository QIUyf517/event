import json
from datetime import date
from pathlib import Path

import pytest
from app.agents.single_agent import SingleResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools

QUESTIONS = json.loads((Path(__file__).parents[1] / "evals" / "questions.json").read_text(encoding="utf-8"))

@pytest.mark.parametrize("question", QUESTIONS, ids=[f"eval-{i:02d}" for i in range(1, 31)])
def test_fixed_evaluation_question_has_auditable_report(tmp_path, question):
    repo = SQLiteRepository(tmp_path / "eval.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    report = SingleResearchAgent(ResearchTools(repo, "demo")).run(question, "510300", date(2026, 9, 28)).final_report
    assert report["data_mode"] == "demo"
    assert report["as_of_date"] == "2026-09-28"
    if report["answer_status"] == "unsupported":
        assert report["data_cutoff_date"] is None
        assert report["calculated_results"]["signal"] == "insufficient_data"
    else:
        assert report["data_cutoff_date"] <= report["as_of_date"]
    assert report["calculated_results"]["algorithm_version"]
    assert report["disclaimer"]
    assert all(item["source_url"] for item in report["evidence"])
    source_dates = report["calculated_results"]["source_dates"]
    assert all(value <= report["as_of_date"] for value in source_dates.values())
    repo.close()
