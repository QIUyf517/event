"""Replay the typed stage-five path on fixed graphs; prepare human review packets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.agents.typed_hypothesis import TypedEventHypothesisDebate
from app.config import PROJECT_ROOT
from app.data.repositories.event_graph import EventGraphStore
from app.llm.client import OpenAICompatibleClient
from app.models.event_graph import EventGraphRun
from app.models.event_research import EventDebateRun
from app.reporting.event_report import public_debate


SOURCE = PROJECT_ROOT / "output/event-phase5-20261013-sources/event_research_phase5_source.sqlite3"
GRAPH_DIR = PROJECT_ROOT / "output/event-phase45-20261023-final-v11"
OUTPUT = PROJECT_ROOT / "output/event-phase5-typed-20261023-v1"
CASES = ("moutai", "pingan", "smic")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review_packet(run, public, *, debate_sha256: str, public_sha256: str):
    graph = run.graph
    claims = {claim.claim_id: claim for claim in graph.claims}
    citations = {item.citation_id: item for item in graph.citations}
    sources = {item.document_id: item for item in graph.sources}
    events = {item.event_id: item for item in graph.events}
    rows = []
    for original, shown in zip(run.hypotheses, public["hypotheses"]):
        evidence = []
        for claim_id in dict.fromkeys(original.support_claim_ids + original.counter_claim_ids):
            claim = claims[claim_id]
            citation = citations[claim.citation_id]
            source = sources[citation.document_id]
            event = events[claim.event_id]
            evidence.append({
                "claim_id": claim_id,
                "role": "support" if claim_id in original.support_claim_ids else "counter",
                "event_title": event.title, "event_date_status": event.date_status,
                "event_occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "source_title": source.title, "source_type": source.source_type,
                "source_published_at": source.published_at.isoformat(),
                "source_url": source.source_url, "reprint_of": source.reprint_of,
                "citation_id": citation.citation_id, "quote": citation.quote,
                "claim_attribute": claim.attribute, "claim_value_text": claim.value_text,
                "numeric_status": claim.numeric_status,
            })
        rows.append({
            "hypothesis_id": original.hypothesis_id,
            "interpretation_kind": original.interpretation_kind,
            "public_statement": shown["statement"],
            "evidence": evidence,
            "semantic_verdict": None,
            "review_notes": "",
        })
    hits = {hit.chunk_id: hit for check in run.search_checks for hit in check.hits}
    findings = [{"hypothesis_id": item.hypothesis_id, "kind": item.kind,
                 "claim_ids": item.claim_ids, "hit_chunk_ids": item.hit_chunk_ids,
                 "hits": [{"chunk_id": hit_id, "title": hits[hit_id].title,
                           "published_at": hits[hit_id].published_at.isoformat(),
                           "excerpt": hits[hit_id].excerpt}
                          for hit_id in item.hit_chunk_ids if hit_id in hits],
                 "semantic_verdict": None, "review_notes": ""}
                for item in run.findings]
    return {
        "status": "pending_human_review", "reviewer": None, "reviewed_at": None,
        "run_id": run.run_id, "graph_run_id": graph.run_id,
        "source_database_sha256": run.source_database_sha256,
        "debate_sha256": debate_sha256, "public_sha256": public_sha256,
        "question": graph.question, "subject_code": graph.subject_code,
        "as_of_date": graph.as_of_date.isoformat(),
        "checks": [
            "逐条确认事件标题、发生日和报道日与原文一致；计划事项不得写成已完成。",
            "逐条确认支持/反对关系成立，数字与统计口径一致，转载未重复计证。",
            "确认候选之间确实互异，并检查反方核查和结论措辞。",
        ],
        "hypotheses": rows,
        "findings": findings,
        "public_conclusion": public["conclusion"],
        "overall_verdict": None,
        "overall_notes": "",
    }


def run(source: Path = SOURCE, graph_dir: Path = GRAPH_DIR, output: Path = OUTPUT,
        cases: tuple[str, ...] = CASES):
    source, graph_dir, output = source.resolve(), graph_dir.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError("输出目录已存在；请使用新的目录保留旧验收记录")
    if not source.is_file():
        raise FileNotFoundError(source)
    fixed = json.loads((graph_dir / "acceptance.json").read_text(encoding="utf-8"))
    source_hash = digest(source)
    if fixed["source_sha256_before"] != source_hash:
        raise ValueError("固定图谱与来源副本的哈希不一致")
    llm = OpenAICompatibleClient(enabled=True)
    if not llm.configured:
        raise RuntimeError("真实模型尚未配置")
    output.mkdir(parents=True)
    os.environ["EVENT_DEBATE_MAX_USD_PER_RUN"] = "0.08"
    result = {"started_at": datetime.now(timezone.utc).isoformat(),
              "source_database": str(source), "source_sha256_before": source_hash,
              "graph_dir": str(graph_dir), "cases": {}, "cost_cap_usd": 0.5,
              "interpretation_schema": "typed_selection_v1"}
    graph_store = EventGraphStore(graph_dir / "event_graph_phase5.sqlite3", readonly=True)
    try:
        for case in cases:
            if case not in CASES:
                raise ValueError(f"未知固定案例：{case}")
            if sum(item.get("cost_usd", 0) for item in result["cases"].values()) + 0.08 > 0.5:
                raise RuntimeError("下一轮预算可能超过总费用上限")
            try:
                graph_file = graph_dir / f"graph_{case}.json"
                graph = EventGraphRun.model_validate_json(graph_file.read_text(encoding="utf-8"))
                saved = graph_store.get(graph.run_id)
                if saved is None or saved.model_dump(mode="json") != graph.model_dump(mode="json"):
                    raise ValueError("固定 JSON 图谱与数据库不一致")
                debate = TypedEventHypothesisDebate(source, graph_store.path,
                            output / "typed_debates.sqlite3", llm).build(graph.run_id)
                public = public_debate(debate)
                debate_file = output / f"debate_{case}.json"
                public_file = output / f"public_{case}.json"
                debate_file.write_text(
                    debate.model_dump_json(indent=2), encoding="utf-8")
                public_file.write_text(
                    json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
                (output / f"review_{case}.json").write_text(
                    json.dumps(review_packet(debate, public,
                        debate_sha256=digest(debate_file), public_sha256=digest(public_file)),
                        ensure_ascii=False, indent=2),
                    encoding="utf-8")
                result["cases"][case] = {
                    "run_id": debate.run_id, "model_tokens": debate.model_tokens,
                    "cost_usd": debate.estimated_cost_usd,
                    "hypotheses": len(debate.hypotheses),
                    "findings": len(debate.findings),
                    "stop_reason": debate.stop_reason,
                    "quality_issues": debate.quality_issues,
                    "human_review": "pending",
                }
            except Exception as exc:
                result["cases"][case] = {"error": f"{type(exc).__name__}: {exc}"}
            result["source_sha256_after"] = digest(source)
            result["source_unchanged"] = result["source_sha256_after"] == source_hash
            result["total_cost_usd"] = round(sum(
                item.get("cost_usd", 0) for item in result["cases"].values()), 8)
            (output / "acceptance.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if not result["source_unchanged"] or result["total_cost_usd"] > 0.5:
                raise RuntimeError("来源哈希变化或超过总费用上限，已停止")
    finally:
        graph_store.close()
    return result


def prepare_saved_reviews(directory: Path):
    """Refresh review templates from saved runs without making model calls."""
    for case in CASES:
        debate_file = directory / f"debate_{case}.json"
        public_file = directory / f"public_{case}.json"
        review_file = directory / f"review_{case}.json"
        if review_file.is_file():
            prior = json.loads(review_file.read_text(encoding="utf-8"))
            if prior.get("reviewer") or prior.get("overall_verdict"):
                raise ValueError(f"{case}已有人工填写内容，拒绝覆盖")
        run = EventDebateRun.model_validate_json(debate_file.read_text(encoding="utf-8"))
        public = public_debate(run)
        public_file.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        review_file.write_text(json.dumps(review_packet(run, public,
            debate_sha256=digest(debate_file), public_sha256=digest(public_file)),
            ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--prepare-saved-reviews", action="store_true")
    args = parser.parse_args()
    if args.prepare_saved_reviews:
        prepare_saved_reviews(args.output_dir)
        print("Review templates prepared from saved reports; no model calls made.")
    else:
        print(json.dumps(run(output=args.output_dir, cases=tuple(args.case or CASES)),
                         ensure_ascii=False, indent=2))
