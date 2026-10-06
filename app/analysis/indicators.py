from app.models.research import MarketBar, ShareRecord

def price_position(bars: list[MarketBar], window: int = 60) -> float | None:
    valid = [b.close for b in bars[-window:] if b.close is not None]
    if not valid:
        return None
    low, high, current = min(valid), max(valid), valid[-1]
    return 0.5 if high == low else (current - low) / (high - low)

def share_change(shares: list[ShareRecord], days: int = 5) -> float | None:
    valid = [r.shares for r in shares if r.shares is not None]
    if len(valid) <= days or valid[-days - 1] == 0:
        return None
    return valid[-1] / valid[-days - 1] - 1

def turnover_percentile(bars: list[MarketBar], window: int = 252) -> float | None:
    valid = [b.turnover for b in bars[-window:] if b.turnover is not None]
    if not valid:
        return None
    current = valid[-1]
    return sum(x <= current for x in valid) / len(valid)


def price_return(bars: list[MarketBar], days: int = 5) -> float | None:
    valid = [bar.close for bar in bars if bar.close is not None and bar.close > 0]
    if len(valid) <= days:
        return None
    return valid[-1] / valid[-days - 1] - 1


def volume_ratio(bars: list[MarketBar], days: int = 5) -> float | None:
    recent = bars[-2 * days:]
    if len(recent) < 2 * days:
        return None
    sources = {bar.source for bar in recent}
    # fetch_akshare 统一将东方财富“手”换算为“份”，新浪直接提供“份”。
    if len(sources) != 1 and not sources <= {"akshare:eastmoney", "akshare:sina"}:
        return None
    volumes = [bar.volume for bar in recent]
    if any(value is None or value < 0 for value in volumes):
        return None
    previous = sum(volumes[:days])
    return sum(volumes[days:]) / previous if previous > 0 else None

