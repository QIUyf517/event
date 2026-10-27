"""Validate manually filled semantic-review records against exact saved outputs."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.models.event_research import EventDebateRun
from scripts.recheck_typed_event_phase5 import CASES, digest


VERDICTS = {"pass", "fail", "uncertain"}


def verify_case(directory: Path, case: str) -> dict:
    decision = json.loads((directory / f"review_{case}.json").read_text(encoding="utf-8"))
    debate_file = directory / f"debate_{case}.json"
    public_file = directory / f"public_{case}.json"
    run = EventDebateRun.model_validate_json(debate_file.read_text(encoding="utf-8"))
    problems = []
    if decision.get("run_id") != run.run_id or decision.get("graph_run_id") != run.graph_run_id:
        problems.append("审核记录与运行编号不一致")
    if decision.get("source_database_sha256") != run.source_database_sha256:
        problems.append("审核记录与来源库哈希不一致")
    if decision.get("debate_sha256") != digest(debate_file):
        problems.append("审核记录与内部报告文件哈希不一致")
    if decision.get("public_sha256") != digest(public_file):
        problems.append("审核记录与公开报告文件哈希不一致")
    rows = decision.get("hypotheses", [])
    if [row.get("hypothesis_id") for row in rows] != [h.hypothesis_id for h in run.hypotheses]:
        problems.append("候选审核行与运行不一致")
    findings = decision.get("findings", [])
    if [(row.get("hypothesis_id"), row.get("kind")) for row in findings] != [
            (f.hypothesis_id, f.kind) for f in run.findings]:
        problems.append("反方审核行与运行不一致")
    reviewer = decision.get("reviewer")
    if decision.get("status") != "completed":
        problems.append("审核状态尚未标记完成")
    if not isinstance(reviewer, str) or not reviewer.strip():
        problems.append("缺少人工审核者")
    try:
        datetime.fromisoformat(decision["reviewed_at"])
    except (KeyError, TypeError, ValueError):
        problems.append("缺少有效审核时间")
    for group, values in (("候选", rows), ("反方", findings)):
        for index, row in enumerate(values, 1):
            if row.get("semantic_verdict") not in VERDICTS:
                problems.append(f"{group}{index}缺少语义判断")
            if row.get("semantic_verdict") != "pass" and not str(row.get("review_notes", "")).strip():
                problems.append(f"{group}{index}未通过或存疑但没有说明")
    overall = decision.get("overall_verdict")
    if overall not in VERDICTS:
        problems.append("缺少整体语义判断")
    elif overall == "pass" and any(row.get("semantic_verdict") != "pass"
                                   for row in rows + findings):
        problems.append("逐条审核未全部通过，整体不能通过")
    if overall != "pass" and not str(decision.get("overall_notes", "")).strip():
        problems.append("整体未通过或存疑但没有说明")
    return {"case": case, "valid": not problems, "overall_verdict": overall,
            "problems": problems}


def verify(directory: Path) -> dict:
    checks = [verify_case(directory, case) for case in CASES]
    return {"human_semantic_acceptance": (
                "passed" if all(item["valid"] and item["overall_verdict"] == "pass"
                for item in checks) else "pending_or_failed"), "cases": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), ensure_ascii=False, indent=2))
