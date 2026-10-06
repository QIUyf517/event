from datetime import date
from uuid import uuid4
from app.config import ALLOWED_ETFS, MAX_SHARE_DATES
from app.data.fetchers import FetchError, fetch_akshare, fetch_sse_shares
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import SyncResult

def sync_market_data(repo: SQLiteRepository, etf_code: str, start_date: date, end_date: date) -> SyncResult:
    """真实模式同步；失败时不写入任何演示数据。"""
    if etf_code not in ALLOWED_ETFS:
        raise ValueError(f"第一阶段仅支持: {', '.join(ALLOWED_ETFS)}")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    warnings = []
    market_rows, share_rows = 0, 0
    try:
        bars = fetch_akshare(etf_code, start_date, end_date)
        repo.upsert_market(bars)
        market_rows, market_status = len(bars), "success"
    except FetchError as exc:
        bars, market_status = [], "failed"
        warnings.append(str(exc))
    # 份额接口可能晚于行情发布。每次同步都重新检查本地最近若干交易日，
    # 避免某日行情已经入库后，缺失份额再也没有补抓机会。
    share_dates = [bar.trade_date for bar in repo.market(etf_code, end_date, limit=MAX_SHARE_DATES)]
    if share_dates:
        try:
            shares, share_warnings = fetch_sse_shares(etf_code, share_dates)
        except Exception as exc:
            shares, share_warnings = [], [f"上交所份额来源失败: {exc}"]
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

