from datetime import date, datetime

from app.data.fetchers import FetchError
from app.data.repositories.sqlite import SQLiteRepository
from app.data.sync import sync_market_data
from app.models.research import MarketBar, ShareRecord

def test_live_market_failure_never_falls_back_to_demo(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    monkeypatch.setattr("app.data.sync.fetch_akshare", lambda *args: (_ for _ in ()).throw(FetchError("offline")))
    result = sync_market_data(repo, "510300", date(2026, 1, 1), date(2026, 1, 31))
    assert result.market_status == "failed"
    assert result.market_rows == 0
    assert result.share_status == "failed"
    assert repo.market("510300", date(2026, 1, 31)) == []
    repo.close()

def test_partial_share_failure_preserves_real_market_only(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    bar = MarketBar(etf_code="510300", trade_date=date(2026, 1, 5), close=4.0, volume=10, turnover=40, source="akshare:eastmoney", fetched_at=datetime.utcnow())
    monkeypatch.setattr("app.data.sync.fetch_akshare", lambda *args: [bar])
    monkeypatch.setattr("app.data.sync.fetch_sse_shares", lambda *args: ([], ["not published"]))
    result = sync_market_data(repo, "510300", date(2026, 1, 1), date(2026, 1, 31))
    assert result.market_status == "success"
    assert result.share_status == "unavailable"
    assert result.market_rows == 1
    assert repo.shares("510300", date(2026, 1, 31)) == []
    repo.close()

def test_real_share_rows_are_idempotent(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    bar = MarketBar(etf_code="510300", trade_date=date(2026, 1, 5), close=4.0, volume=10, turnover=40, source="akshare:eastmoney", fetched_at=datetime.utcnow())
    share = ShareRecord(etf_code="510300", trade_date=date(2026, 1, 5), shares=1000, source="sse:test", fetched_at=datetime.utcnow())
    monkeypatch.setattr("app.data.sync.fetch_akshare", lambda *args: [bar])
    monkeypatch.setattr("app.data.sync.fetch_sse_shares", lambda *args: ([share], []))
    sync_market_data(repo, "510300", date(2026, 1, 1), date(2026, 1, 31))
    sync_market_data(repo, "510300", date(2026, 1, 1), date(2026, 1, 31))
    assert repo.conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM etf_shares").fetchone()[0] == 1
    repo.close()
