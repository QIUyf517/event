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

