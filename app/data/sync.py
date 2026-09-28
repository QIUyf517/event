from datetime import date
from uuid import uuid4
from app.config import MAX_SHARE_DATES
from app.data.fetchers import FetchError, fetch_akshare, fetch_sse_shares
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import SyncResult

def sync_market_data(repo: SQLiteRepository, etf_code: str, start_date: date, end_date: date) -> SyncResult:
    """真实模式同步；失败时不写入任何演示数据。"""
    warnings = []
    market_rows, share_rows = 0, 0
    try:
        bars = fetch_akshare(etf_code, start_date, end_date)
        repo.upsert_market(bars)
        market_rows, market_status = len(bars), "success"
    except FetchError as exc:
        bars, market_status = [], "failed"
        warnings.append(str(exc))
    if bars:
        share_dates = [bar.trade_date for bar in bars[-MAX_SHARE_DATES:]]
        shares, share_warnings = fetch_sse_shares(etf_code, share_dates)
        warnings.extend(share_warnings)
        if shares:
            repo.upsert_shares(shares)
            share_rows = len(shares)
            shares_status = "success" if len(shares) == len(share_dates) else "partial"
        else:
            shares_status = "unavailable"
    else:
        shares_status = "failed"
    result = SyncResult(sync_id=str(uuid4()), etf_code=etf_code, start_date=start_date, end_date=end_date, market_status=market_status, share_status=shares_status, market_rows=market_rows, share_rows=share_rows, warnings=warnings)
    repo.save_sync_result(result)
    return result

