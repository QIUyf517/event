from datetime import date, datetime, timedelta
import math
import random
from app.models.research import Document, ETFProfile, MarketBar, ShareRecord

class FetchError(RuntimeError):
    pass

def demo_profile() -> ETFProfile:
    return ETFProfile(etf_code="510300", name="沪深300ETF", fund_company="演示基金管理公司", index_name="沪深300指数")

def demo_market(end_date: date, days: int = 90) -> list[MarketBar]:
    random.seed(300)
    rows, price = [], 3.85
    for i in range(days):
        d = end_date - timedelta(days=days - i - 1)
        if d.weekday() >= 5:
            continue
        price *= 1 + 0.0015 * math.sin(i / 4) + random.uniform(-0.012, 0.012)
        turnover = 1.2e9 * (1 + 0.25 * math.sin(i / 7) + random.uniform(-0.1, 0.1))
        rows.append(MarketBar(etf_code="510300", trade_date=d, close=round(price, 4), volume=turnover / price, turnover=round(turnover, 2), source="demo_synthetic", fetched_at=datetime.utcnow()))
    return rows

def demo_shares(end_date: date, days: int = 90) -> list[ShareRecord]:
    rows = []
    for i in range(days):
        d = end_date - timedelta(days=days - i - 1)
        if d.weekday() >= 5:
            continue
        rows.append(ShareRecord(etf_code="510300", trade_date=d, shares=1.05e11 + i * 1.8e8, source="demo_synthetic", fetched_at=datetime.utcnow()))
    return rows

def demo_documents() -> list[Document]:
    return [Document(document_id="demo-510300-report", title="沪深300ETF定期报告（演示资料）", content="本文件为演示资料。基金报告披露基金规模、份额和跟踪指数情况。投资者应关注数据日期与披露口径。", source_url="https://example.com/demo/510300-report", publisher="演示资料库", published_at=date.today(), fetched_at=datetime.utcnow(), document_type="fund_report", etf_codes=["510300"])]

def fetch_akshare(etf_code: str, start_date: date, end_date: date):
    """可选适配器：AKShare字段会随接口变化，失败时由调用方降级。"""
    try:
        import akshare as ak
        data = ak.fund_etf_hist_em(symbol=etf_code, start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"), adjust="")
        return [MarketBar(etf_code=etf_code, trade_date=date.fromisoformat(str(r["日期"])), close=float(r["收盘"]), volume=float(r.get("成交量", 0) or 0), turnover=float(r.get("成交额", 0) or 0), source="akshare", fetched_at=datetime.utcnow()) for _, r in data.iterrows()]
    except Exception as exc:
        raise FetchError(f"AKShare行情采集失败: {exc}") from exc

