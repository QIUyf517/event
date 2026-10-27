"""对 20 道合成场景题的运行结果进行交互式人工复核。

脚本并排展示已保存的原始答案和未提供给模型的评分标准。
它不自行判定结果，也不调用模型；每项结论均须由具名复核者输入。
每次判定后保存复核状态，以便后续继续。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation.event_research import load_event_cases


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "evals/event_phase6_results_20261023.json"
RUNS = ROOT / "evals/event_phase6_runs_20261023.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewer", required=True, help="Human reviewer's name")
    parser.add_argument("--output", type=Path, default=ROOT / "output/event-phase6-human-review.json")
    parser.add_argument("--method", choices=["rules", "single", "multi"])
    parser.add_argument("--repeat", type=int, choices=[1, 2])
    args = parser.parse_args()
    reviewer = args.reviewer.strip()
    if not reviewer:
        parser.error("reviewer must be a named human")
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    raw = {record["file"]: record["raw_json"]
           for record in json.loads(RUNS.read_text(encoding="utf-8"))}
    cases = {case.id: case for case in load_event_cases()}
    saved = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else []
    reviewed = {(item["case_id"], item["method"], item["repeat"]): item for item in saved}
    if any(item["reviewer"] != reviewer for item in saved):
        parser.error("existing review file belongs to another reviewer; choose another --output")
    for row in index["rows"]:
        if args.method and row["method"] != args.method:
            continue
        if args.repeat and row["repeat"] != args.repeat:
            continue
        key = (row["case_id"], row["method"], row["repeat"])
        if key in reviewed:
            continue
        encoded = raw[row["file"]].encode("utf-8")
        if sha256(encoded).hexdigest() != row["sha256"]:
            raise ValueError(f"stored run hash changed: {row['file']}")
        run = json.loads(encoded)
        case = cases[row["case_id"]]
        print("\n" + "=" * 72)
        print(f"{case.id} | {row['method']} | repeat {row['repeat']} | {row['status']}")
        print("Question:", case.question)
        print("Synthetic input:", json.dumps(run["input"]["records"], ensure_ascii=False, indent=2))
        print("Saved answer:", json.dumps(run.get("answer"), ensure_ascii=False, indent=2))
        print("Gold answer:", case.gold_answer)
        print("Expected status:", ", ".join(case.expected_statuses))
        print("Expected event labels:", ", ".join(e.event_key for e in case.expected_events) or "none")
        print("Forbidden conclusions:", "; ".join(case.forbidden_conclusions) or "none")
        print("Review notes:", "; ".join(case.manual_review_notes) or "none")
        print("Machine metrics:", json.dumps(run.get("metrics"), ensure_ascii=False))
        while True:
            verdict = input("Human verdict [pass/fail/uncertain/skip/quit]: ").strip().lower()
            if verdict in {"pass", "fail", "uncertain", "skip", "quit"}:
                break
        if verdict == "quit":
            break
        if verdict == "skip":
            continue
        notes = input("Reason / precise unsupported sentence (required): ").strip()
        if not notes:
            print("No reason entered; this item stays pending.")
            continue
        item = {"case_id": case.id, "method": row["method"], "repeat": row["repeat"],
                "file": row["file"], "sha256": row["sha256"], "reviewer": reviewer,
                "status": verdict, "notes": notes,
                "reviewed_at": datetime.now(timezone.utc).isoformat()}
        reviewed[key] = item
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(list(reviewed.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.output)
        print(f"Saved {len(reviewed)} human decisions to {args.output}")


if __name__ == "__main__":
    main()
