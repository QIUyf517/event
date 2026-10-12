"""Print an offline audit with synthetic and real-source results kept separate."""

import argparse
import json
import sys
from pathlib import Path

from app.api.main import ACCEPTED_EVENT_SAMPLE_PATH
from app.evaluation.event_suites import audit_event_suites


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="核对企业事件研究合成题和真实快照的验收边界")
    parser.add_argument("--database", type=Path, default=ACCEPTED_EVENT_SAMPLE_PATH)
    args = parser.parse_args()
    print(json.dumps(audit_event_suites(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
