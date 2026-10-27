"""Run phase-four extraction against an isolated copy of the phase-three snapshot.

This command never changes the source database and never collects online data.
It makes bounded model calls for the selected cases and writes only to output_dir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.agents.event_graph_builder import EventGraphBuilder
from app.agents.event_orchestrator import EventPlanningRun, EventResearchOrchestrator
from app.config import PROJECT_ROOT
from app.data.repositories.event_graph import EventGraphStore
from app.llm.client import OpenAICompatibleClient


DEFAULT_SOURCE = PROJECT_ROOT / "output/event-phase3-20261012-acceptance/event_research_phase3.sqlite3"
DEFAULT_AUDIT = PROJECT_ROOT / "output/event-phase3-20261012-acceptance/planning_audit.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output/event-phase4-20261012-acceptance"
START, END = date(2026, 8, 14), date(2026, 9, 13)
CASES = {
    "moutai": ("600519", "按时间梳理贵州茅台最近30天的披露和新闻事件，找出可核对的原文。"),
    "pingan": ("601318", "梳理中国平安最近30天半年度业绩披露与新闻事件，逐条核对原文。"),
    "smic": ("688981", "梳理中芯国际最近30天半年度业绩披露与新闻事件，逐条核对原文。"),
}


class SavedExtractionBuilder(EventGraphBuilder):
    """Recheck saved model output after deterministic grouping changes."""

    def __init__(self, source_database, graph_database, raw_items, prior_graph):
        super().__init__(source_database, graph_database)
        self.raw_items = raw_items
        self.prior_graph = prior_graph

    def _extract(self, planning, chunks):
        self.last_raw_items = self.raw_items
        extraction_cost = max(0.0, self.prior_graph.estimated_cost_usd
                              - planning.estimated_cost_usd)
        return (self.raw_items, self.prior_graph.extraction_tokens,
                extraction_cost, self.prior_graph.model)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def backup_sqlite(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"目标库已存在：{target}")
    original = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        original.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("阶段四资料副本完整性检查失败")
    finally:
        copy.close()
        original.close()


def validate_saved_graph(graph, source_copy: Path, graph_path: Path) -> dict:
    source = sqlite3.connect(source_copy.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        for citation in graph.citations:
            row = source.execute("SELECT content FROM document_chunks WHERE chunk_id=? AND document_id=?",
                                 (citation.chunk_id, citation.document_id)).fetchone()
            if row is None or row["content"][citation.start_offset:citation.end_offset] != citation.quote:
                raise ValueError(f"引用 {citation.citation_id} 无法逐字回到原文")
    finally:
        source.close()
    store = EventGraphStore(graph_path, readonly=True)
    try:
        integrity = store.conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = store.conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError("图谱库完整性或引用关系检查失败")
        if store.get(graph.run_id) is None:
            raise RuntimeError("保存后无法重新读取事件图谱")
    finally:
        store.close()
    return {"integrity": integrity, "foreign_key_violations": len(foreign_keys),
            "citations_rechecked": len(graph.citations)}


def main() -> None:
    parser = argparse.ArgumentParser(description="在独立副本上验收企业事件时间线")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--planning-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--resume", action="store_true", help="续跑已建立且来源哈希匹配的验收目录")
    parser.add_argument("--rerun", action="store_true", help="重新调用模型处理已保存的指定案例")
    parser.add_argument("--reprocess-saved-extraction", action="store_true",
                        help="只重算已保存的模型提取结果；不发起新模型请求")
    args = parser.parse_args()
    if args.reprocess_saved_extraction and not args.resume:
        parser.error("重算已保存结果需要 --resume")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    source = args.source.resolve()
    output = args.output_dir.resolve()
    if not source.is_file() or not args.planning_audit.is_file():
        raise SystemExit("阶段三资料库或规划记录不存在")
    if output == source or output == source.parent or source.parent in output.parents:
        raise SystemExit("阶段四输出目录必须与阶段三资料库分开")
    source_hash = digest(source)
    report_path = output / "acceptance.json"
    source_copy = output / "event_research_phase4_source.sqlite3"
    graph_path = output / "event_graph_phase4.sqlite3"
    if output.exists() and not args.resume:
        raise SystemExit("目标目录已存在；如需续跑请显式使用 --resume")
    if not output.exists():
        output.mkdir(parents=True)
        backup_sqlite(source, source_copy)
        report = {"started_at": datetime.now(timezone.utc).isoformat(),
                  "source_database": str(source), "source_sha256_before": source_hash,
                  "source_copy": str(source_copy), "cases": {}}
    else:
        if not source_copy.is_file() or not report_path.is_file():
            raise SystemExit("续跑目录缺少资料副本或验收清单")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("source_sha256_before") != source_hash:
            raise SystemExit("阶段三原库哈希与首次验收不一致，拒绝续跑")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    llm = OpenAICompatibleClient(enabled=True)
    if not llm.configured and not args.reprocess_saved_extraction:
        raise SystemExit("真实模型尚未配置；资料副本已建立，配置后使用 --resume 续跑")
    audit = json.loads(args.planning_audit.read_text(encoding="utf-8"))
    audited = next((item["run"] for item in audit["cases"] if item["case_id"] == "event_timeline"), None)
    if audited is None:
        raise SystemExit("阶段三规划记录缺少 event_timeline 案例")
    chosen = args.case or list(CASES)
    for case_id in chosen:
        if (case_id in report["cases"] and not args.rerun and not args.reprocess_saved_extraction
                and report["cases"][case_id].get("graph_id")):
            continue
        subject, question = CASES[case_id]
        try:
            planning_path = output / f"planning_{case_id}.json"
            if args.resume and planning_path.is_file():
                planning = EventPlanningRun.model_validate_json(
                    planning_path.read_text(encoding="utf-8"))
            else:
                planning = (EventPlanningRun.model_validate(audited) if case_id == "moutai"
                            else EventResearchOrchestrator(source_copy, llm).run(question, subject, END, START))
            planning_path.write_text(
                planning.model_dump_json(indent=2), encoding="utf-8")
            if args.reprocess_saved_extraction:
                prior_graph = json.loads((output / f"graph_{case_id}.json").read_text(encoding="utf-8"))
                raw_items = json.loads((output / f"extraction_raw_{case_id}.json").read_text(encoding="utf-8"))
                from app.models.event_graph import EventGraphRun
                builder = SavedExtractionBuilder(source_copy, graph_path, raw_items,
                                                 EventGraphRun.model_validate(prior_graph))
            else:
                builder = EventGraphBuilder(source_copy, graph_path, llm)
            graph = builder.build(planning)
            (output / f"extraction_raw_{case_id}.json").write_text(
                json.dumps(builder.last_raw_items, ensure_ascii=False, indent=2), encoding="utf-8")
            (output / f"graph_{case_id}.json").write_text(
                graph.model_dump_json(indent=2), encoding="utf-8")
            checks = validate_saved_graph(graph, source_copy, graph_path)
            report["cases"][case_id] = {"graph_id": graph.run_id, "status": graph.status,
                "events": len(graph.events), "sources": len(graph.sources),
                "citations": len(graph.citations), "conflicts": len(graph.conflicts),
                "warnings": graph.warnings, "model": graph.model,
                "tokens": graph.planning_tokens + graph.extraction_tokens,
                "estimated_cost_usd": graph.estimated_cost_usd,
                "reprocessed_from_saved_extraction": args.reprocess_saved_extraction,
                **checks}
        except Exception as exc:
            report["cases"][case_id] = {"error": f"{type(exc).__name__}: {exc}"}
        report["source_sha256_after"] = digest(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report.get("source_unchanged") is not True:
        raise SystemExit("阶段三原库哈希发生变化")
    print(json.dumps({"cases": report["cases"], "source_unchanged": True,
                      "output_dir": str(output)}, ensure_ascii=False, indent=2))
    if any("error" in report["cases"].get(case_id, {}) for case_id in chosen):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
