"""Check the fixed historical sample against a reviewed event/fact ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from app.agents.event_graph_builder import _occurrence_date_supported
from app.models.event_graph import EventGraphRun


def verify(source: Path, graph_dir: Path, gold_path: Path) -> dict:
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != gold["source_database_sha256"]:
        errors.append("资料库哈希与人工核对底稿不一致")
    conn = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for name, case in gold["cases"].items():
            graph = EventGraphRun.model_validate_json(
                (graph_dir / f"graph_{name}.json").read_text(encoding="utf-8"))
            if graph.subject_code != case["subject_code"]:
                errors.append(f"{name}: 公司代码不一致")
            if (graph.window_start.isoformat() != case["window_start"] or
                    graph.as_of_date.isoformat() != case["as_of_date"]):
                errors.append(f"{name}: 研究窗口不一致")
            citations = {item.citation_id: item for item in graph.citations}
            facts = {(claim.attribute, claim.value_text,
                      citations[claim.citation_id].quote,
                      citations[claim.citation_id].document_id)
                     for claim in graph.claims}
            for reviewed in case["reviewed_events"]:
                for fact in reviewed["baseline_facts"]:
                    key = (fact["attribute"], fact["value_text"], fact["quote"],
                           fact["document_id"])
                    if key not in facts:
                        errors.append(f"{name}: 丢失已核对事实 {reviewed['baseline_title']} / {fact['attribute']}")
                row = conn.execute(
                    "SELECT content,document_id FROM document_chunks WHERE chunk_id=?",
                    (reviewed["source_chunk_id"],)).fetchone()
                if (row is None or row["document_id"] != reviewed["source_document_id"]
                        or reviewed["source_quote"] not in row["content"]):
                    errors.append(f"{name}: 底稿原文失去定位 {reviewed['baseline_title']}")
            for citation in graph.citations:
                row = conn.execute(
                    "SELECT content,document_id FROM document_chunks WHERE chunk_id=?",
                    (citation.chunk_id,)).fetchone()
                if (row is None or row["document_id"] != citation.document_id or
                        row["content"][citation.start_offset:citation.end_offset] != citation.quote):
                    errors.append(f"{name}: 图谱引文无法逐字定位 {citation.citation_id}")
            expected = case["expected_final"]
            if len(graph.events) != expected["event_count"]:
                errors.append(f"{name}: 事件数 {len(graph.events)} 不等于已核对的 {expected['event_count']}")
            titles = {event.title: event for event in graph.events}
            for title in expected["required_titles"]:
                if title not in titles:
                    errors.append(f"{name}: 缺少重要事件 {title}")
            for title, day in expected["known_dates"].items():
                event = titles.get(title)
                if event is None or event.occurred_at is None or event.occurred_at.isoformat() != day:
                    errors.append(f"{name}: 已知动作日期缺失或错误 {title} / {day}")
            values = {claim.value_text for claim in graph.claims}
            for value in expected["required_values"]:
                if value not in values:
                    errors.append(f"{name}: 缺少重要原文数值 {value}")
            if len(graph.conflicts) < expected["minimum_conflicts"]:
                errors.append(f"{name}: 待核实冲突被遗漏")
            claims = {claim.claim_id: claim for claim in graph.claims}
            for event in graph.events:
                if event.occurred_at is None:
                    continue
                if not any(_occurrence_date_supported(
                        citations[claims[claim_id].citation_id].quote, event.occurred_at)
                        for claim_id in event.claim_ids):
                    errors.append(f"{name}: 日期缺少动作原文 {event.title}")
            for event in graph.events:
                if "预定" in event.title and event.occurred_at is not None:
                    errors.append(f"{name}: 预定事项误写为已完成 {event.title}")
    finally:
        conn.close()
    return {"passed": not errors, "source_sha256": digest,
            "reviewed_events": sum(len(c["reviewed_events"]) for c in gold["cases"].values()),
            "reviewed_facts": sum(len(e["baseline_facts"])
                                  for c in gold["cases"].values()
                                  for e in c["reviewed_events"]),
            "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=Path("evals/event_phase45_gold.json"))
    arguments = parser.parse_args()
    result = verify(arguments.source, arguments.graph_dir, arguments.gold)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
