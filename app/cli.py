import argparse
import json
from datetime import date, timedelta

from app.agents.single_agent import SingleResearchAgent
from app.config import DATA_MODE, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo, seed_profile
from app.data.sync import sync_market_data
from app.doctor import run_checks
from app.rag.ingest import ingest_local, ingest_official_url
from app.tools.research_tools import ResearchTools

DEFAULT_QUESTION = "分析510300最近20个交易日的资金信号，并结合公开资料解释可能原因"

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
    for name in ("ingest-local", "ingest-url"):
        ingest = sub.add_parser(name)
        ingest.add_argument("source")
        ingest.add_argument("--title", required=True)
        ingest.add_argument("--publisher", required=True)
        ingest.add_argument("--published-at", type=parse_date, required=True)
        ingest.add_argument("--source-url", default="")
        ingest.add_argument("--type", dest="document_type", choices=["fund_announcement", "fund_report", "policy", "index_document"], required=True)
        ingest.add_argument("--etf", default="510300")
    return parser

def main():
    args = build_parser().parse_args()
    if args.command == "doctor":
        print(json.dumps(run_checks(), ensure_ascii=False, indent=2))
        return
    if args.command == "seed-demo" and args.mode != "demo":
        raise SystemExit("seed-demo 只能写入demo数据库")
    if args.command == "sync-market" and args.mode != "live":
        raise SystemExit("sync-market 必须使用 --mode live")
    repo = SQLiteRepository(database_path_for_mode(args.mode))
    try:
        if args.command == "seed-demo":
            seed_demo(repo, args.as_of)
            result = {"status": "success", "mode": "demo", "as_of_date": args.as_of.isoformat()}
        elif args.command == "sync-market":
            seed_profile(repo)
            result = sync_market_data(repo, args.etf, args.start, args.end).model_dump(mode="json")
        elif args.command == "research":
            if args.mode == "demo" and repo.profile(args.etf) is None:
                raise SystemExit("demo数据库为空，请先运行: etf-research --mode demo seed-demo")
            if args.mode == "live":
                seed_profile(repo)
            result = SingleResearchAgent(ResearchTools(repo, args.mode)).run(args.question, args.etf, args.as_of).final_report
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
