import argparse
from datetime import date
from app.config import DATABASE_PATH, DATA_MODE
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.data.sync import sync_market_data
from app.agents.single_agent import SingleResearchAgent
from app.tools.research_tools import ResearchTools

def main():
    parser = argparse.ArgumentParser(description="A股ETF研究助手")
    parser.add_argument("command", choices=["demo", "research"])
    parser.add_argument("question", nargs="?", default="分析510300最近20个交易日的资金信号，并结合公开资料解释可能原因")
    args = parser.parse_args()
    repo = SQLiteRepository(DATABASE_PATH)
    seed_demo(repo, date.today())
    if DATA_MODE in {"auto", "live"}:
        sync_market_data(repo, "510300", date.today(), date.today())
    state = SingleResearchAgent(ResearchTools(repo)).run(args.question)
    print(state.final_report)
    repo.close()

if __name__ == "__main__": main()

