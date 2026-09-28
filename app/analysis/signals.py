from app.config import ALGORITHM_VERSION
from app.analysis.indicators import price_position, share_change, turnover_percentile
from app.models.research import MarketBar, ShareRecord, SignalResult

def calculate_signal(etf_code: str, as_of_date, bars: list[MarketBar], shares: list[ShareRecord], data_mode: str = "demo") -> SignalResult:
    indicators = {"price_position_60d": price_position(bars), "share_change_5d": share_change(shares), "turnover_percentile": turnover_percentile(bars)}
    missing = [k for k, v in indicators.items() if v is None]
    if missing:
        signal, strength = "insufficient_data", "insufficient"
    else:
        pp, sc, tp = indicators.values()
        score = (1 if pp >= 0.5 else -1) + (1 if sc >= 0 else -1) + (1 if tp >= 0.5 else -1)
        signal = "positive" if score >= 2 else "moderately_positive" if score == 1 else "neutral" if score == 0 else "cautious"
        strength = "strong" if abs(score) == 3 else "medium"
    source_dates = {}
    if bars: source_dates["market"] = bars[-1].trade_date
    if shares: source_dates["shares"] = shares[-1].trade_date
    return SignalResult(etf_code=etf_code, as_of_date=as_of_date, indicators=indicators, signal=signal, evidence_strength=strength, missing_fields=missing, source_dates=source_dates, algorithm_version=ALGORITHM_VERSION, data_mode=data_mode, source_records={"market": len(bars), "shares": len(shares)})

