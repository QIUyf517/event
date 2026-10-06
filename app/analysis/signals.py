"""五项可复核观察的启发式共振；不表示实际资金净流入。"""

from app.config import ALGORITHM_VERSION
from app.analysis.indicators import price_position, price_return, share_change, turnover_percentile, volume_ratio
from app.models.research import MarketBar, ShareRecord, SignalResult


def _direction(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def calculate_signal(etf_code: str, as_of_date, bars: list[MarketBar], shares: list[ShareRecord], data_mode: str = "demo") -> SignalResult:
    indicators = {
        "price_position_60d": price_position(bars),
        "share_change_5d": share_change(shares),
        "turnover_percentile": turnover_percentile(bars),
        "price_return_5d": price_return(bars),
        "volume_ratio_5d": volume_ratio(bars),
    }
    missing = [key for key, value in indicators.items() if value is None]
    triggers = []
    if missing:
        signal, strength = "insufficient_data", "insufficient"
    else:
        pp = indicators["price_position_60d"]
        sc = indicators["share_change_5d"]
        tp = indicators["turnover_percentile"]
        pr = indicators["price_return_5d"]
        vr = indicators["volume_ratio_5d"]
        price_direction = _direction(pr)
        votes = {
            "price_position_60d": 1 if pp >= 0.6 else -1 if pp <= 0.4 else 0,
            "share_change_5d": _direction(sc),
            "turnover_percentile": price_direction if tp >= 0.7 else 0,
            "price_return_5d": price_direction,
            "volume_ratio_5d": price_direction if vr >= 1.2 else 0,
        }
        reasons = {
            "price_position_60d": "近60个可用交易日的价格区间位置",
            "share_change_5d": "最近5个可用披露日份额变化，仅为申赎代理信号",
            "turnover_percentile": "成交额进入历史高分位时，按价格方向辅助确认",
            "price_return_5d": "最近5个可用收盘价记录的价格变化",
            "volume_ratio_5d": "近5个交易日成交量相对前5日放大时，按价格方向辅助确认",
        }
        triggers = [{"indicator": key, "value": value, "contribution": votes[key], "reason": reasons[key]}
                    for key, value in indicators.items()]
        score = sum(votes.values())
        signal = ("positive" if score >= 3 and votes["share_change_5d"] >= 0
                  else "moderately_positive" if score >= 1 else "neutral" if score == 0 else "cautious")
        strength = "strong" if abs(score) >= 4 else "medium" if abs(score) >= 2 else "weak"
    source_dates = {}
    if bars:
        source_dates["market"] = bars[-1].trade_date
    if shares:
        source_dates["shares"] = shares[-1].trade_date
    latest_values = {"close": bars[-1].close if bars else None,
                     "turnover": bars[-1].turnover if bars else None,
                     "shares": shares[-1].shares if shares else None}
    return SignalResult(etf_code=etf_code, as_of_date=as_of_date, indicators=indicators,
        signal=signal, evidence_strength=strength, missing_fields=missing,
        source_dates=source_dates, algorithm_version=ALGORITHM_VERSION,
        data_mode=data_mode, source_records={"market": len(bars), "shares": len(shares)},
        latest_values=latest_values, trigger_details=triggers)
