"""生成可保存、可比较的研究流程评估结果。"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

from app.agents.single_agent import SingleResearchAgent
from app.config import PROJECT_ROOT
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


QUESTIONS_PATH = PROJECT_ROOT / "evals" / "questions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "evaluation" / "latest.json"


def load_questions(path: Path = QUESTIONS_PATH) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_questions(database_path: Path, as_of_date: date,
                       questions: list[str] | None = None) -> dict:
    questions = questions or load_questions()
    repo = SQLiteRepository(database_path)
    started = datetime.now().astimezone()
    results = []
    try:
        seed_demo(repo, as_of_date)
        agent = SingleResearchAgent(ResearchTools(repo, "demo"))
        for index, question in enumerate(questions, 1):
            item_started = time.perf_counter()
            failures = []
            answer_status = None
            try:
                report = agent.run(question, "510300", as_of_date).final_report
                answer_status = report["answer_status"]
                if report["data_mode"] != "demo":
                    failures.append("数据模式不是demo")
                if str(report["as_of_date"]) != as_of_date.isoformat():
                    failures.append("查询日期不一致")
                if not report.get("calculated_results", {}).get("algorithm_version"):
                    failures.append("缺少算法版本")
                if not report.get("disclaimer"):
                    failures.append("缺少使用边界")
                if any(not item.get("source_url") for item in report.get("evidence", [])):
                    failures.append("证据缺少来源地址")
                if any(str(value) > as_of_date.isoformat() for value in
                       report.get("calculated_results", {}).get("source_dates", {}).values()):
                    failures.append("使用了查询日之后的数据")
                if answer_status == "unsupported" and report.get("data_cutoff_date") is not None:
                    failures.append("不支持的问题不应伪造数据截止日期")
            except Exception as exc:
                failures.append(f"执行异常: {exc}")
            results.append({"id": f"eval-{index:02d}", "question": question,
                "passed": not failures, "answer_status": answer_status,
                "duration_ms": round((time.perf_counter() - item_started) * 1000),
                "failures": failures})
    finally:
        repo.close()
    passed = sum(item["passed"] for item in results)
    durations = [item["duration_ms"] for item in results]
    return {"generated_at": datetime.now().astimezone().isoformat(),
        "started_at": started.isoformat(), "as_of_date": as_of_date.isoformat(),
        "dataset": str(QUESTIONS_PATH.relative_to(PROJECT_ROOT)), "total": len(results),
        "passed": passed, "failed": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0,
        "average_duration_ms": round(sum(durations) / len(durations)) if durations else 0,
        "max_duration_ms": max(durations, default=0), "items": results}


def save_evaluation(result: dict, output_path: Path = DEFAULT_OUTPUT) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
