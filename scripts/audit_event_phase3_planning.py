"""Run four bounded real-model planning cases on the isolated saved snapshot."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone

from app.agents.event_orchestrator import EventResearchOrchestrator
from app.llm.client import OpenAICompatibleClient
from scripts.accept_event_research_phase3 import TARGET, TARGET_DIR, START, END


CASES = [
    ("event_timeline", "按时间梳理贵州茅台最近30天的披露和新闻事件，找出可核对的原文。"),
    ("announcement_interpretation", "解读贵州茅台最近30天的公司公告与财务披露，以原文为准。"),
    ("financial_change", "核对贵州茅台最近30天财务披露中的经营变化，并查看同期股价与成交量。"),
    ("causal_followup", "媒体报道的变化是否可能与同期股价波动有关？请查公告、新闻和行情，保留相反证据，不直接断言因果。"),
]


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(f"缺少阶段三验收库：{TARGET}")
    llm = OpenAICompatibleClient(enabled=True)
    if not llm.configured:
        raise RuntimeError("未配置真实模型")
    output_path = TARGET_DIR / "planning_audit.json"
    records = json.loads(output_path.read_text(encoding="utf-8"))["cases"] if output_path.exists() else []
    for case_id, question in CASES:
        if len(sys.argv) > 1 and case_id not in sys.argv[1:]:
            continue
        records = [record for record in records if record["case_id"] != case_id]
        try:
            run = EventResearchOrchestrator(TARGET, llm).run(question, "600519", END, START)
            records.append({"case_id": case_id, "question": question, "run": run.model_dump(mode="json")})
            print(f"{case_id}: {run.status}; tools={[s.tool for s in run.plan.steps]}; tokens={run.total_tokens}; estimated_usd={run.estimated_cost_usd}")
        except Exception as exc:
            records.append({"case_id": case_id, "question": question,
                            "error": f"{type(exc).__name__}: {exc}"})
            print(f"{case_id}: ERROR {type(exc).__name__}: {exc}")
        output_path.write_text(json.dumps({
            "recorded_at": datetime.now(timezone.utc).isoformat(), "database": str(TARGET),
            "cases": records, "note": "Only plan/evidence acceptance; no answer correctness score."},
            ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
