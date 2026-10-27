"""企业资料采集与事件研究的本地命令，由用户显式触发。"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app.agents.event_graph_builder import EventGraphBuilder
from app.agents.event_orchestrator import EventResearchOrchestrator
from app.config import EVENT_GRAPH_DATABASE_PATH, EVENT_RESEARCH_DATABASE_PATH
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.rag.ingest import collect_event_document
from app.tools.research_tools import EVENT_SUBJECTS


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股企业事件研究")
    parser.add_argument("--database", type=Path, default=EVENT_RESEARCH_DATABASE_PATH,
                        help="企业原文资料库路径")
    parser.add_argument("--graph-database", type=Path, default=EVENT_GRAPH_DATABASE_PATH,
                        help="独立事件图谱库路径")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect-event-source", help="联网采集一份准入清单中的企业资料")
    collect.add_argument("url")
    collect.add_argument("--source-id", required=True)
    collect.add_argument("--subject", choices=sorted(EVENT_SUBJECTS), required=True)
    collect.add_argument("--title", required=True)
    collect.add_argument("--publisher", required=True)
    collect.add_argument("--published-at", type=parse_date, required=True)
    collect.add_argument("--material-type", choices=["announcement", "news", "financial"])
    collect.add_argument("--snapshot-dir", type=Path)
    collect.add_argument("--reprint-of")

    for name in ("search-event", "plan-event", "build-event-timeline"):
        command = commands.add_parser(name)
        command.add_argument("question")
        command.add_argument("--subject", choices=sorted(EVENT_SUBJECTS), required=True)
        command.add_argument("--start", type=parse_date, required=True)
        command.add_argument("--as-of", type=parse_date, required=True)
        if name == "search-event":
            command.add_argument("--type", dest="source_type",
                                 choices=["announcement", "news", "financial"])
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if args.command == "collect-event-source":
        repo = SQLiteRepository(args.database)
        try:
            seed_event_research_subjects(repo)
            document, stored = collect_event_document(repo, args.url, args.source_id,
                snapshot_dir=args.snapshot_dir, subject_id=args.subject, title=args.title,
                publisher=args.publisher, published_at=args.published_at,
                reprint_of=args.reprint_of, material_type=args.material_type)
            result = {"status": stored.status, "document": document.model_dump(mode="json"),
                      "canonical_document_id": stored.canonical_document_id,
                      "duplicate_reason": stored.duplicate_reason}
        finally:
            repo.close()
    else:
        if not args.database.is_file():
            raise SystemExit("企业资料库不存在；先采集资料或指定 --database")
        if args.start > args.as_of:
            raise SystemExit("研究开始日期不能晚于截止日期")
        if args.command == "search-event":
            repo = SQLiteRepository.open_readonly(args.database)
            try:
                source_types = [args.source_type] if args.source_type else None
                result = {"subject": args.subject, "as_of_date": args.as_of.isoformat(),
                          "items": [item.model_dump(mode="json") for item in repo.search_event_chunks(
                              args.question, args.subject, args.as_of, args.start, source_types)]}
            finally:
                repo.close()
        else:
            planning = EventResearchOrchestrator(args.database).run(
                args.question, args.subject, args.as_of, args.start)
            if args.command == "plan-event":
                result = planning.model_dump(mode="json")
            else:
                graph = EventGraphBuilder(args.database, args.graph_database).build(planning)
                result = graph.model_dump(mode="json")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
