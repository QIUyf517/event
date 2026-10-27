"""在已保存来源的只读副本上，运行受限的第五阶段真实模型用例。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.agents.event_graph_builder import EventGraphBuilder
from app.agents.event_orchestrator import EventPlanningRun, EventResearchOrchestrator
from app.agents.hypothesis_debate import EventHypothesisDebate
from app.config import PROJECT_ROOT
from app.llm.client import OpenAICompatibleClient
from scripts.prepare_event_phase5_sources import digest


SOURCE = PROJECT_ROOT / "output/event-phase5-20261013-sources/event_research_phase5_source.sqlite3"
OUTPUT = PROJECT_ROOT / "output/event-phase5-20261013-acceptance"
START, END = date(2026, 8, 13), date(2026, 9, 12)
CASES = {
    "moutai": ("600519", "核对贵州茅台半年度披露及董事会公告，哪些事件有明确发生日期？讨论可能的解释与反证。"),
    "pingan": ("601318", "核对中国平安半年度披露及董事会公告，哪些事件有明确发生日期？讨论可能的解释与反证。"),
    "smic": ("688981", "核对中芯国际半年度披露与股权激励公告，哪些事件有明确发生日期？讨论可能的解释与反证。"),
}


def run(source: Path, output: Path, *, resume: bool = False,
        cases: list[str] | None = None, planning_dir: Path | None = None,
        extraction_dir: Path | None = None, graph_dir: Path | None = None) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_file() or source == output or source.parent == output or source.parent in output.parents:
        raise ValueError("来源库不存在或验收输出目录与资料副本重叠")
    source_hash = digest(source)
    audit_path = output / "acceptance.json"
    graph_path = output / "event_graph_phase5.sqlite3"
    debate_path = output / "event_debate_phase5.sqlite3"
    if output.exists():
        if not resume or not audit_path.is_file():
            raise FileExistsError("验收目录已存在；核对后使用 --resume")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit["source_sha256_before"] != source_hash:
            raise ValueError("来源副本哈希已变化，拒绝续跑")
    else:
        output.mkdir(parents=True)
        audit = {"started_at": datetime.now(timezone.utc).isoformat(),
                 "source_database": str(source), "source_sha256_before": source_hash,
                 "graph_database": str(graph_path), "debate_database": str(debate_path),
                 "cases": {}}
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    llm = OpenAICompatibleClient(enabled=True)
    if not llm.configured:
        raise RuntimeError("真实模型尚未配置；验收目录已建立，可配置后续跑")
    for case_id in cases or list(CASES):
        prior = audit["cases"].get(case_id, {})
        if prior.get("debate_run_id"):
            continue
        subject, question = CASES[case_id]
        builder = None
        try:
            planning_file = output / f"planning_{case_id}.json"
            graph_file = output / f"graph_{case_id}.json"
            saved_graph_file = (graph_dir / f"graph_{case_id}.json"
                                if graph_dir is not None else None)
            reusing_saved_graph = saved_graph_file is not None and saved_graph_file.is_file()
            prior_graph_audit = {}
            if reusing_saved_graph:
                prior_graph_audit = json.loads(
                    (graph_dir / "acceptance.json").read_text(encoding="utf-8"))
                if prior_graph_audit.get("source_sha256_before") != source_hash:
                    raise ValueError("复用事件图谱的来源库哈希不一致")
                planning = None
                planning_status = prior_graph_audit.get("cases", {}).get(case_id, {}).get(
                    "planning_status", "reused_graph")
            elif planning_file.is_file():
                planning = EventPlanningRun.model_validate_json(planning_file.read_text(encoding="utf-8"))
                planning_status = planning.status
            elif planning_dir is not None:
                saved = planning_dir / f"planning_{case_id}.json"
                planning = EventPlanningRun.model_validate_json(saved.read_text(encoding="utf-8"))
                if ((planning.subject_code, planning.window_start, planning.as_of_date, planning.question)
                        != (subject, START, END, question)):
                    raise ValueError("复用的规划与本次问题或日期不一致")
                planning_file.write_text(planning.model_dump_json(indent=2), encoding="utf-8")
                planning_status = planning.status
            else:
                planning = EventResearchOrchestrator(source, llm).run(question, subject, END, START)
                planning_file.write_text(planning.model_dump_json(indent=2), encoding="utf-8")
                planning_status = planning.status
            if graph_file.is_file():
                from app.models.event_graph import EventGraphRun
                graph = EventGraphRun.model_validate_json(graph_file.read_text(encoding="utf-8"))
            elif reusing_saved_graph:
                from app.data.repositories.event_graph import EventGraphStore
                from app.models.event_graph import EventGraphRun

                graph = EventGraphRun.model_validate_json(
                    saved_graph_file.read_text(encoding="utf-8"))
                if ((graph.subject_code, graph.window_start, graph.as_of_date, graph.question)
                        != (subject, START, END, question)):
                    raise ValueError("复用事件图谱的研究范围不一致")
                store = EventGraphStore(graph_path)
                try:
                    store.save(graph)
                finally:
                    store.close()
                graph_file.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
            else:
                prior_raw = (extraction_dir / f"extraction_raw_{case_id}.json"
                             if extraction_dir is not None else None)
                prior_graph_file = (extraction_dir / f"graph_{case_id}.json"
                                    if extraction_dir is not None else None)
                if prior_raw is not None and prior_raw.is_file() and prior_graph_file.is_file():
                    from app.models.event_graph import EventGraphRun
                    from scripts.accept_event_research_phase4 import SavedExtractionBuilder

                    prior_audit = json.loads((extraction_dir / "acceptance.json").read_text(encoding="utf-8"))
                    if prior_audit.get("source_sha256_before") != source_hash:
                        raise ValueError("复用事件提取结果的来源库哈希不一致")
                    old_graph = EventGraphRun.model_validate_json(prior_graph_file.read_text(encoding="utf-8"))
                    if ((old_graph.subject_code, old_graph.window_start, old_graph.as_of_date,
                         old_graph.question) != (subject, START, END, question)):
                        raise ValueError("复用事件提取结果的研究范围不一致")
                    builder = SavedExtractionBuilder(source, graph_path,
                        json.loads(prior_raw.read_text(encoding="utf-8")), old_graph)
                else:
                    builder = EventGraphBuilder(source, graph_path, llm)
                graph = builder.build(planning)
                (output / f"extraction_raw_{case_id}.json").write_text(
                    json.dumps(builder.last_raw_items, ensure_ascii=False, indent=2), encoding="utf-8")
                graph_file.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
            debate = EventHypothesisDebate(source, graph_path, debate_path, llm).build(graph.run_id)
            (output / f"debate_{case_id}.json").write_text(
                debate.model_dump_json(indent=2), encoding="utf-8")
            audit["cases"][case_id] = {"subject_code": subject,
                "planning_status": planning_status, "graph_run_id": graph.run_id,
                "graph_status": graph.status, "graph_events": len(graph.events),
                "graph_sources": len(graph.sources),
                "announcement_sources": sum(item.source_type == "announcement" for item in graph.sources),
                "known_event_dates": sum(item.date_status == "known" for item in graph.events),
                "graph_citations": len(graph.citations), "graph_conflicts": len(graph.conflicts),
                "graph_warnings": graph.warnings,
                "debate_run_id": debate.run_id,
                "debate_status": debate.conclusion.answer_status,
                "debate_level": debate.conclusion.level,
                "hypotheses": len(debate.hypotheses), "counter_findings": len(debate.findings),
                "quality_issues": debate.quality_issues,
                "model_tokens": (debate.model_tokens if reusing_saved_graph else
                                 planning.total_tokens + graph.extraction_tokens + debate.model_tokens),
                "estimated_cost_usd": round(debate.estimated_cost_usd if reusing_saved_graph else
                                            graph.estimated_cost_usd + debate.estimated_cost_usd, 8)}
        except Exception as exc:
            if builder is not None and getattr(builder, "last_raw_items", None):
                (output / f"extraction_partial_{case_id}.json").write_text(
                    json.dumps(builder.last_raw_items, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            audit["cases"][case_id] = {**prior, "error": f"{type(exc).__name__}: {exc}"}
        audit["source_sha256_after"] = digest(source)
        audit["source_unchanged"] = audit["source_sha256_after"] == source_hash
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        if not audit["source_unchanged"]:
            raise RuntimeError("验收期间来源资料库哈希发生变化")
    return audit


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="固定快照的阶段五真实模型验收")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--planning-dir", type=Path, default=None,
                        help="复用相同问题与日期的已保存规划，不再次调用规划模型")
    parser.add_argument("--extraction-dir", type=Path, default=None,
                        help="若有匹配的已保存提取结果，只重算确定性图谱，不再调用提取模型")
    parser.add_argument("--graph-dir", type=Path, default=None,
                        help="若有匹配的已保存图谱，复制到新结果库并只重跑第五阶段模型")
    args = parser.parse_args()
    result = run(args.source, args.output_dir, resume=args.resume,
                 cases=args.case, planning_dir=args.planning_dir,
                 extraction_dir=args.extraction_dir, graph_dir=args.graph_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any("error" in value for value in result["cases"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
