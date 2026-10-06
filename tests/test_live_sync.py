from datetime import date, datetime
from types import SimpleNamespace
import sys

import pandas as pd

from app.data.fetchers import FetchError, fetch_akshare, fetch_sse_shares
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


def test_share_backfill_rechecks_recent_stored_market_dates(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    old_bar = MarketBar(etf_code="510300", trade_date=date(2026, 1, 5), close=4.0,
        volume=10, turnover=40, source="akshare:eastmoney", fetched_at=datetime.utcnow())
    new_bar = old_bar.model_copy(update={"trade_date": date(2026, 1, 6), "close": 4.1})
    repo.upsert_market([old_bar])
    requested_dates = []

    monkeypatch.setattr("app.data.sync.fetch_akshare", lambda *args: [new_bar])
    def fake_shares(etf_code, trade_dates):
        requested_dates.extend(trade_dates)
        return ([ShareRecord(etf_code=etf_code, trade_date=day, shares=1000,
            source="sse:test", fetched_at=datetime.utcnow()) for day in trade_dates], [])
    monkeypatch.setattr("app.data.sync.fetch_sse_shares", fake_shares)

    result = sync_market_data(repo, "510300", date(2026, 1, 6), date(2026, 1, 6))
    assert requested_dates == [date(2026, 1, 5), date(2026, 1, 6)]
    assert result.share_status == "success"
    assert len(repo.shares("510300", date(2026, 1, 6))) == 2
    repo.close()


def test_share_backfill_still_runs_when_market_source_temporarily_fails(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    bar = MarketBar(etf_code="510500", trade_date=date(2026, 1, 5), close=4.0,
        volume=10, turnover=40, source="akshare:sina", fetched_at=datetime.utcnow())
    share = ShareRecord(etf_code="510500", trade_date=bar.trade_date, shares=1000,
        source="sse:test", fetched_at=datetime.utcnow())
    repo.upsert_market([bar])
    monkeypatch.setattr("app.data.sync.fetch_akshare",
        lambda *args: (_ for _ in ()).throw(FetchError("offline")))
    monkeypatch.setattr("app.data.sync.fetch_sse_shares", lambda *args: ([share], []))

    result = sync_market_data(repo, "510500", bar.trade_date, bar.trade_date)
    assert result.market_status == "failed"
    assert result.share_status == "success"
    assert repo.shares("510500", bar.trade_date)[-1].shares == 1000
    repo.close()


def test_market_source_schema_change_uses_real_sina_fallback(monkeypatch):
    fake_ak = SimpleNamespace(
        fund_etf_hist_em=lambda **kwargs: pd.DataFrame([{"日期": "2026-01-05", "收盘": 3.0}]),
        fund_etf_hist_sina=lambda **kwargs: pd.DataFrame([{"date": "2026-01-05", "close": 3.0, "volume": 100.0, "amount": 300.0}]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    rows = fetch_akshare("510050", date(2026, 1, 5), date(2026, 1, 5))
    assert len(rows) == 1
    assert rows[0].source == "akshare:sina"
    assert rows[0].etf_code == "510050"


def test_unexpected_share_source_error_keeps_market_and_records_warning(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "live.sqlite3")
    bar = MarketBar(etf_code="510500", trade_date=date(2026, 1, 5), close=4.0, volume=10, turnover=40, source="akshare:sina", fetched_at=datetime.utcnow())
    monkeypatch.setattr("app.data.sync.fetch_akshare", lambda *args: [bar])
    monkeypatch.setattr("app.data.sync.fetch_sse_shares", lambda *args: (_ for _ in ()).throw(RuntimeError("temporarily unavailable")))
    result = sync_market_data(repo, "510500", date(2026, 1, 5), date(2026, 1, 5))
    assert result.market_status == "success"
    assert result.share_status == "unavailable"
    assert result.warnings
    assert len(repo.market("510500", date(2026, 1, 5))) == 1
    repo.close()


def test_sse_share_response_for_wrong_date_is_ignored(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"result": [{"SEC_CODE": "510500", "STAT_DATE": "2026-01-02", "TOT_VOL": "100"}]}
    monkeypatch.setattr("app.data.fetchers.requests.get", lambda *args, **kwargs: Response())
    rows, warnings = fetch_sse_shares("510500", [date(2026, 1, 5)], pause_seconds=0)
    assert rows == []
    assert "其他日期" in warnings[0]
