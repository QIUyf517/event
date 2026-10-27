"""Run the controlled synthetic benchmark; --live explicitly enables paid calls."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation.phase6 import run_experiment

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--budget-usd", type=float, default=.5)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 0 < args.budget_usd <= 5:
        parser.error("repeats须为1至5，budget须大于0且不超过5美元")
    result = run_experiment(args.output, args.repeats,
                            ("rules", "single", "multi") if args.live else ("rules",), args.budget_usd)
    print(f"Saved {len(result['rows'])} runs; review pending: {result['human_review_pending']}")
