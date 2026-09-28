from datetime import date, datetime
from app.analysis.indicators import price_position, share_change, turnover_percentile
from app.models.research import MarketBar, ShareRecord
from app.analysis.signals import calculate_signal

def test_indicators_are_deterministic():
    now = datetime(2026, 1, 1)
    bars = [MarketBar(etf_code="510300", trade_date=date(2026, 1, i + 1), close=i + 1, volume=1, turnover=i + 1, source="test", fetched_at=now) for i in range(10)]
    shares = [ShareRecord(etf_code="510300", trade_date=date(2026, 1, i + 1), shares=100 + i, source="test", fetched_at=now) for i in range(10)]
    assert price_position(bars, 10) == 1.0
    assert round(share_change(shares, 5), 6) == round(109 / 104 - 1, 6)
    assert turnover_percentile(bars, 10) == 1.0

def test_missing_values_return_none():
    assert price_position([], 60) is None
    assert share_change([], 5) is None
    assert turnover_percentile([], 252) is None

def test_missing_shares_never_produces_fund_flow_signal():
    now = datetime(2026, 1, 1)
    bars = [MarketBar(etf_code="510300", trade_date=date(2026, 1, i + 1), close=i + 1, volume=1, turnover=i + 1, source="test", fetched_at=now) for i in range(10)]
    signal = calculate_signal("510300", date(2026, 1, 10), bars, [], "live")
    assert signal.signal == "insufficient_data"
    assert "share_change_5d" in signal.missing_fields

