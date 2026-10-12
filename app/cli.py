import argparse
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from app.agents.single_agent import SingleResearchAgent
from app.agents.llm_research import ModelDrivenResearchAgent
from app.config import ALLOWED_ETFS, DATA_MODE, EVENT_RESEARCH_DATABASE_PATH, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo, seed_event_research_subjects, seed_profile
from app.data.sync import sync_market_data
from app.doctor import run_checks
from app.evaluation.service import DEFAULT_OUTPUT, evaluate_questions, save_evaluation
from app.monitoring.service import compare_etfs, run_monitor_once
from app.rag.ingest import collect_event_document, ingest_local, ingest_official_url
from app.tools.research_tools import EventResearchTools, ResearchTools

DEFAULT_QUESTION = "分析当前资金信号，并结合已入库公开资料说明证据和局限"

def parse_date(value: str) -> date:
    return date.fromisoformat(value)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股ETF研究助手")
    parser.add_argument("--mode", choices=["demo", "live"], default=DATA_MODE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    seed = sub.add_parser("seed-demo")
    seed.add_argument("--as-of", type=parse_date, default=date.today())
    sync = sub.add_parser("sync-market")
    sync.add_argument("--etf", default="510300")
    sync.add_argument("--start", type=parse_date, default=date.today() - timedelta(days=120))
    sync.add_argument("--end", type=parse_date, default=date.today())
    research = sub.add_parser("research")
    research.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    research.add_argument("--etf", default="510300")
    research.add_argument("--as-of", type=parse_date, default=date.today())
    research.add_argument("--method", choices=["rules", "ai"], default="rules",
                          help="ai 会显式调用已配置的大模型并产生服务费用")
    monitor = sub.add_parser("monitor-once", help="手动执行一次三只ETF监控")
    monitor.add_argument("--as-of", type=parse_date, default=date.today())
    monitor.add_argument("--no-sync", action="store_true", help="仅使用本地数据，不联网同步")
    comparison = sub.add_parser("compare", help="按同一查询日期对比ETF")
    comparison.add_argument("--etfs", nargs="+", default=list(ALLOWED_ETFS))
    comparison.add_argument("--as-of", type=parse_date, default=date.today())
    history = sub.add_parser("signal-history", help="查看已生成的历史信号")
    history.add_argument("--etf", default="510300")
    history.add_argument("--start", type=parse_date, default=date.today() - timedelta(days=120))
    history.add_argument("--end", type=parse_date, default=date.today())
    alerts = sub.add_parser("alerts", help="查看本地提醒")
    alerts.add_argument("--limit", type=int, default=50)
    evaluate = sub.add_parser("evaluate", help="运行固定问题评估并保存JSON结果")
    evaluate.add_argument("--as-of", type=parse_date, default=date.today())
    evaluate.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    for name in ("ingest-local", "ingest-url"):
        ingest = sub.add_parser(name)
        ingest.add_argument("source")
        ingest.add_argument("--title", required=True)
        ingest.add_argument("--publisher", required=True)
        ingest.add_argument("--published-at", type=parse_date, required=True)
        ingest.add_argument("--source-url", default="")
        ingest.add_argument("--type", dest="document_type", choices=["fund_announcement", "fund_report", "policy", "index_document"], required=True)
        ingest.add_argument("--etf", default="510300")
    collect = sub.add_parser("collect-event-source", help="用户手动触发公司公告、新闻或财务资料采集")
    collect.add_argument("url")
    collect.add_argument("--source-id", required=True)
    collect.add_argument("--subject", choices=["600519", "601318", "688981"], required=True)
    collect.add_argument("--title", required=True)
    collect.add_argument("--publisher", required=True)
    collect.add_argument("--published-at", type=parse_date, required=True)
    collect.add_argument("--material-type", choices=["announcement", "news", "financial"])
    collect.add_argument("--snapshot-dir", type=Path)
    collect.add_argument("--reprint-of")
    search_event = sub.add_parser("search-event", help="按研究截止日只读检索公司事件资料")
    search_event.add_argument("query")
    search_event.add_argument("--subject", choices=["600519", "601318", "688981"], required=True)
    search_event.add_argument("--start", type=parse_date, required=True)
    search_event.add_argument("--as-of", type=parse_date, required=True)
    search_event.add_argument("--type", dest="source_type", choices=["announcement", "news", "financial"])
    return parser

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if args.command == "doctor":
        print(json.dumps(run_checks(), ensure_ascii=False, indent=2))
        return
    if args.command == "seed-demo" and args.mode != "demo":
        raise SystemExit("seed-demo 只能写入demo数据库")
    if args.command == "sync-market" and args.mode != "live":
        raise SystemExit("sync-market 必须使用 --mode live")
    if args.command == "monitor-once" and args.mode != "live" and not args.no_sync:
        raise SystemExit("演示模式监控须使用 --no-sync")
    if hasattr(args, "etf") and args.etf not in ALLOWED_ETFS:
        raise SystemExit(f"第一阶段仅支持: {', '.join(ALLOWED_ETFS)}")
    if args.command == "compare" and any(code not in ALLOWED_ETFS for code in args.etfs):
        raise SystemExit(f"仅支持: {', '.join(ALLOWED_ETFS)}")
    if args.command == "evaluate":
        with tempfile.TemporaryDirectory(prefix="etf-evaluation-") as temp_dir:
            evaluation = evaluate_questions(Path(temp_dir) / "evaluation.sqlite3", args.as_of)
        output = save_evaluation(evaluation, args.output)
        print(json.dumps({"status": "success", "output": str(output), **evaluation},
                         ensure_ascii=False, indent=2, default=str))
        return
    event_command = args.command in {"collect-event-source", "search-event"}
    if args.command == "search-event":
        if not EVENT_RESEARCH_DATABASE_PATH.is_file():
            raise SystemExit("事件资料库不存在，请先运行collect-event-source")
        repo = SQLiteRepository.open_readonly(EVENT_RESEARCH_DATABASE_PATH)
    else:
        repo = SQLiteRepository(EVENT_RESEARCH_DATABASE_PATH if event_command else database_path_for_mode(args.mode))
    try:
        if event_command:
            if args.command == "collect-event-source":
                seed_event_research_subjects(repo)
                document, stored = collect_event_document(repo, args.url, args.source_id,
                    snapshot_dir=args.snapshot_dir, subject_id=args.subject, title=args.title,
                    publisher=args.publisher, published_at=args.published_at,
                    reprint_of=args.reprint_of, material_type=args.material_type)
                result = {"status": stored.status, "document": document.model_dump(mode="json"),
                          "canonical_document_id": stored.canonical_document_id,
                          "duplicate_reason": stored.duplicate_reason}
            else:
                source_types = [args.source_type] if args.source_type else None
                result = {"subject": args.subject, "as_of_date": args.as_of.isoformat(),
                    "items": [item.model_dump(mode="json") for item in repo.search_event_chunks(
                        args.query, args.subject, args.as_of, args.start, source_types)]}
        elif args.command == "seed-demo":
            seed_demo(repo, args.as_of)
            result = {"status": "success", "mode": "demo", "as_of_date": args.as_of.isoformat()}
        elif args.command == "sync-market":
            seed_profile(repo, args.etf)
            result = sync_market_data(repo, args.etf, args.start, args.end).model_dump(mode="json")
        elif args.command == "research":
            if args.mode == "demo" and repo.profile(args.etf) is None:
                raise SystemExit("demo数据库为空，请先运行: etf-research --mode demo seed-demo")
            if args.mode == "live":
                seed_profile(repo, args.etf)
            agent = (ModelDrivenResearchAgent(ResearchTools(repo, args.mode)) if args.method == "ai"
                     else SingleResearchAgent(ResearchTools(repo, args.mode)))
            result = agent.run(args.question, args.etf, args.as_of).final_report
        elif args.command == "monitor-once":
            result = run_monitor_once(repo, args.as_of, args.mode, sync_live=not args.no_sync).model_dump(mode="json")
        elif args.command == "compare":
            result = compare_etfs(repo, args.etfs, args.as_of, args.mode)
        elif args.command == "signal-history":
            from app.config import ALGORITHM_VERSION
            result = [item.model_dump(mode="json") for item in repo.signal_history(
                args.etf, args.start, args.end, ALGORITHM_VERSION)]
        elif args.command == "alerts":
            result = [item.model_dump(mode="json") for item in repo.recent_monitor_alerts(args.limit)]
        else:
            metadata = {"title": args.title, "publisher": args.publisher, "published_at": args.published_at, "document_type": args.document_type, "etf_code": args.etf}
            if args.command == "ingest-local":
                metadata["source_url"] = args.source_url
                document = ingest_local(repo, args.source, **metadata)
            else:
                document = ingest_official_url(repo, args.source, **metadata)
            result = {"status": "success", "document": document.model_dump(mode="json")}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        repo.close()

if __name__ == "__main__":
    main()
