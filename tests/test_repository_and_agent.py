from datetime import date
from app.agents.single_agent import SingleResearchAgent
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools
from app.models.research import MarketBar
from datetime import datetime

def test_demo_pipeline(tmp_path):
    repo = SQLiteRepository(tmp_path / "test.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    state = SingleResearchAgent(ResearchTools(repo, "demo")).run("510300 定期报告 资金信号", "510300", date(2026, 9, 28))
    assert state.final_report["etf"]["etf_code"] == "510300"
    assert state.final_report["as_of_date"] == "2026-09-28"
    assert state.final_report["disclaimer"]
    assert state.final_report["data_mode"] == "demo"
    assert state.document_results
    repo.close()

def test_upsert_is_idempotent_and_unsupported_etf_is_rejected(tmp_path):
    repo = SQLiteRepository(tmp_path / "test.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    seed_demo(repo, date(2026, 9, 28))
    count = repo.conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0]
    assert count > 0
    from app.tools.research_tools import ResearchTools
    import pytest
    with pytest.raises(ValueError):
        ResearchTools(repo).get_etf_profile("510050")
    repo.close()

def test_repository_never_reads_future_market_rows(tmp_path):
    repo = SQLiteRepository(tmp_path / "test.sqlite3")
    repo.upsert_market([
        MarketBar(etf_code="510300", trade_date=date(2026, 1, 2), close=1, volume=1, turnover=1, source="test", fetched_at=datetime.utcnow()),
        MarketBar(etf_code="510300", trade_date=date(2026, 1, 3), close=99, volume=99, turnover=99, source="future", fetched_at=datetime.utcnow()),
    ])
    rows = repo.market("510300", date(2026, 1, 2))
    assert [row.trade_date for row in rows] == [date(2026, 1, 2)]
    repo.close()

