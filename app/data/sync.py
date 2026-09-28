from datetime import date
from app.data.fetchers import FetchError, demo_market, demo_shares, fetch_akshare
from app.data.repositories.sqlite import SQLiteRepository

def sync_market_data(repo: SQLiteRepository, etf_code: str, start_date: date, end_date: date) -> dict:
    """优先使用AKShare；网络、依赖或字段变化时保留可审计的演示降级状态。"""
    try:
        bars = fetch_akshare(etf_code, start_date, end_date)
        repo.upsert_market(bars)
        source, status = "akshare", "success"
    except FetchError as exc:
        bars = demo_market(end_date)
        repo.upsert_market(bars)
        source, status = "demo_synthetic", "fallback"
        warning = str(exc)
    try:
        shares = demo_shares(end_date)
        repo.upsert_shares(shares)
        shares_status = "demo_synthetic"
    except Exception as exc:
        shares_status = "failed"
        warning = str(exc)
    result = {"market_source": source, "market_status": status, "shares_source": shares_status, "as_of_date": end_date.isoformat()}
    if "warning" in locals():
        result["warning"] = warning
    return result

