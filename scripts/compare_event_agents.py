"""Real-source, paired single/multi hypothesis comparison on saved graphs."""
import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agents.hypothesis_debate import EventHypothesisDebate
from app.agents.single_event_agent import SingleEventAgent
from app.data.repositories.event_graph import EventGraphStore
from app.llm.client import OpenAICompatibleClient
from app.models.event_graph import EventGraphRun
from app.reporting.event_report import public_debate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--budget-usd", type=float, default=.5)
    args = parser.parse_args()
    if args.output.exists() or not args.source.is_file():
        parser.error("来源不存在或输出目录已存在")
    if not 1 <= args.repeats <= 5 or not 0 < args.budget_usd <= 5:
        parser.error("轮次须为1至5，预算须大于0且不超过5美元")
    args.output.mkdir(parents=True)
    source_hash = sha256(args.source.read_bytes()).hexdigest()
    graphs = [EventGraphRun.model_validate_json(path.read_text(encoding="utf-8"))
              for path in sorted(args.graph_dir.glob("graph_*.json"))]
    if not graphs:
        parser.error("目录中没有graph_*.json")
    store = EventGraphStore(args.output / "graphs.sqlite3")
    for graph in graphs:
        store.save(graph)
    store.close()
    import os
    # Both treatments use the same explicit per-run cap.
    cap = min(.08, args.budget_usd / (len(graphs) * args.repeats * 2))
    os.environ["EVENT_DEBATE_MAX_USD_PER_RUN"] = str(cap)
    rows, cost = [], 0.0
    for repeat in range(args.repeats):
        for graph in graphs:
            methods = [("single", SingleEventAgent), ("multi", EventHypothesisDebate)]
            if repeat % 2:
                methods.reverse()
            for method, cls in methods:
                if cost + cap > args.budget_usd + 1e-9:
                    raise RuntimeError("剩余实验预算不足一次调用上限")
                started = monotonic()
                runner = cls(args.source, args.output / "graphs.sqlite3", args.output / "debates.sqlite3",
                             OpenAICompatibleClient(enabled=True))
                run = runner.build(graph.run_id)
                cost += run.estimated_cost_usd
                filename = f"{graph.subject_code}-{method}-{repeat+1}.json"
                payload = {"method": method, "repeat": repeat+1, "elapsed_seconds": monotonic()-started,
                           "public": public_debate(run), "internal_draft": run.model_dump(mode="json"),
                           "human_review": {"status": "pending", "reviewer": None}}
                encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode()
                (args.output / filename).write_bytes(encoded)
                rows.append({"subject_code": graph.subject_code, "method": method, "repeat": repeat+1,
                             "file": filename, "sha256": sha256(encoded).hexdigest(),
                             "graph_run_id": graph.run_id, "model_calls": runner.calls,
                             "tokens": run.model_tokens, "estimated_cost_usd": run.estimated_cost_usd,
                             "seconds": payload["elapsed_seconds"], "hypotheses": len(run.hypotheses),
                             "counter_findings": len(run.findings), "quality_issues": len(run.quality_issues),
                             "raw_answer_status": run.conclusion.answer_status, "human_review": "pending"})
                summary = {"scope": "hypothesis_stage_ablation_on_identical_saved_real_graphs",
                           "source_sha256_before": source_hash,
                           "source_sha256_after": sha256(args.source.read_bytes()).hexdigest(),
                           "estimated_cost_usd": cost, "rows": rows,
                           "limitations": ["固定相同图谱，仅比较假设阶段；不代表端到端单Agent与多Agent差异。",
                                           "工具选择与事件覆盖由共享图谱固定，不能作为本消融的改善指标。",
                                           "反方意见更多不表示更正确；内部草稿仅供人工核对，不对外发布。",
                                           "所有语义审核仍待人工完成，不能据此宣称多Agent提高准确率。"]}
                (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"{graph.subject_code} {method} repeat={repeat+1} calls={runner.calls}", flush=True)


if __name__ == "__main__":
    main()
