"""将当前确定性质量规则重新应用于已保存的第五阶段模型输出。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.agents.hypothesis_debate import EventHypothesisDebate
from app.config import PROJECT_ROOT
from app.data.repositories.event_debate import EventDebateStore
from app.data.repositories.sqlite import SQLiteRepository
from app.models.event_research import EventDebateRun
from scripts.prepare_event_phase5_sources import digest


SOURCE = PROJECT_ROOT / "output/event-phase5-20261013-sources/event_research_phase5_source.sqlite3"
SAVED = PROJECT_ROOT / "output/event-phase5-20261013-acceptance-v5"
OUTPUT = PROJECT_ROOT / "output/event-phase5-20261013-quality-recheck"


def run(source: Path, saved: Path, output: Path) -> dict:
    source, saved, output = source.resolve(), saved.resolve(), output.resolve()
    if output.exists() or not source.is_file() or not saved.is_dir():
        raise ValueError("输出目录已存在，或来源资料/已保存运行不存在")
    if output == source.parent or source.parent in output.parents:
        raise ValueError("重算输出目录不能位于来源库目录中")
    output.mkdir(parents=True)
    source_hash = digest(source)
    saved_audit = json.loads((saved / "acceptance.json").read_text(encoding="utf-8"))
    if saved_audit.get("source_sha256_before") != source_hash:
        raise ValueError("已保存验收记录的来源库哈希不一致")
    repo = SQLiteRepository.open_readonly(source)
    report = {"rechecked_at": datetime.now(timezone.utc).isoformat(),
              "source_database_sha256": source_hash,
              "saved_run_dir": str(saved), "new_model_calls": 0, "cases": {}}
    store = EventDebateStore(output / "event_debate_quality_recheck.sqlite3")
    try:
        for case_id in ("moutai", "pingan", "smic"):
            prior = EventDebateRun.model_validate_json(
                (saved / f"debate_{case_id}.json").read_text(encoding="utf-8"))
            if prior.source_database_sha256 and prior.source_database_sha256 != source_hash:
                raise ValueError(f"{case_id} 已保存报告的来源库哈希不一致")
            gate = EventHypothesisDebate(source,
                saved / "event_graph_phase5.sqlite3", output / "event_debate_quality_recheck.sqlite3")
            issues = gate._verify_graph_bytes(repo, prior.graph)
            market_rows = repo.company_market(prior.graph.subject_code, prior.graph.window_start,
                                              prior.graph.as_of_date, limit=31)
            hypotheses = [hypothesis.model_copy(deep=True) for hypothesis in prior.hypotheses]
            conclusion = gate._quality_gate(prior.graph, hypotheses, prior.findings,
                market_rows, issues, prior.conclusion.selected_hypothesis_id)
            if prior.stop_reason != "完成反方核验":
                conclusion.level = "insufficient" if hypotheses else "cannot_judge"
                conclusion.answer_status = "partial" if hypotheses else "insufficient_evidence"
                conclusion.selected_hypothesis_id = None
                conclusion.text = "模型核验中止，已保留可追溯的候选解释；当前不能形成完整判断。"
                issues.append(f"原模型停止原因：{prior.stop_reason}")
                conclusion.limitations = list(dict.fromkeys(issues))
            current = prior.model_copy(update={
                "run_id": f"debate-{uuid4().hex}",
                "created_at": datetime.now(timezone.utc),
                "source_database_sha256": source_hash,
                "hypotheses": hypotheses,
                "conclusion": conclusion,
                "quality_issues": list(dict.fromkeys(issues)),
                "stop_reason": f"已保存模型输出重算质量门；原停止原因：{prior.stop_reason}",
            })
            current = EventDebateRun.model_validate(current.model_dump())
            store.save(current)
            (output / f"debate_{case_id}.json").write_text(
                current.model_dump_json(indent=2), encoding="utf-8")
            report["cases"][case_id] = {"prior_run_id": prior.run_id,
                "rechecked_run_id": current.run_id,
                "graph_run_id": current.graph_run_id,
                "status": current.conclusion.answer_status,
                "level": current.conclusion.level,
                "hypotheses": len(current.hypotheses),
                "findings": len(current.findings),
                "issues": current.quality_issues,
                "original_model_tokens": prior.model_tokens,
                "original_estimated_cost_usd": prior.estimated_cost_usd}
        report["database_integrity"] = store.conn.execute("PRAGMA integrity_check").fetchone()[0]
        report["source_unchanged"] = digest(source) == source_hash
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["database_integrity"] != "ok" or not report["source_unchanged"]:
            raise RuntimeError("质量门重算后的数据库或原资料完整性检查失败")
        return report
    finally:
        store.close()
        repo.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="从已保存模型输出重算阶段五质量门")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--saved", type=Path, default=SAVED)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.saved, args.output_dir),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
