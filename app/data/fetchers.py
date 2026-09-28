from datetime import date, datetime, timedelta
import math
import random
import time
import requests
from app.config import REQUEST_TIMEOUT
from app.models.research import Document, ETFProfile, MarketBar, ShareRecord

class FetchError(RuntimeError):
    pass

def demo_profile() -> ETFProfile:
    return ETFProfile(etf_code="510300", name="沪深300ETF", fund_company="华泰柏瑞基金管理有限公司", index_name="沪深300指数", source="manual_verified")

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
    """读取ETF日线；东方财富失败时降级到新浪，二者都是真实公开数据。"""
    import akshare as ak
    errors = []
    for attempt in range(3):
        try:
            data = ak.fund_etf_hist_em(symbol=etf_code, period="daily", start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"), adjust="")
            break
        except Exception as exc:
            errors.append(f"eastmoney:{exc}")
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
    else:
        data = None
    try:
        rows = []
        fetched_at = datetime.utcnow()
        if data is not None and not data.empty:
            required = {"日期", "收盘", "成交量", "成交额"}
            missing = required.difference(data.columns)
            if missing:
                raise FetchError(f"东方财富字段变化，缺少: {sorted(missing)}")
            source = "akshare:eastmoney"
            iterator = ((r["日期"], r["收盘"], float(r["成交量"]) * 100, r["成交额"]) for _, r in data.iterrows())
        else:
            sina = ak.fund_etf_hist_sina(symbol=f"sh{etf_code}")
            required = {"date", "close", "volume", "amount"}
            missing = required.difference(sina.columns)
            if missing:
                raise FetchError(f"新浪字段变化，缺少: {sorted(missing)}")
            source = "akshare:sina"
            iterator = ((r["date"], r["close"], r["volume"], r["amount"]) for _, r in sina.iterrows())
        for raw_date, raw_close, raw_volume, raw_turnover in iterator:
            trade_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10])
            if trade_date < start_date or trade_date > end_date:
                continue
            close, volume, turnover = float(raw_close), float(raw_volume), float(raw_turnover)
            if close <= 0 or volume < 0 or turnover < 0:
                raise FetchError(f"行情数值异常: {trade_date}")
            rows.append(MarketBar(etf_code=etf_code, trade_date=trade_date, close=close, volume=volume, turnover=turnover, source=source, fetched_at=fetched_at))
        if not rows:
            raise FetchError(f"两个真实行情来源均未返回指定日期数据: {errors}")
        return rows
    except Exception as exc:
        if isinstance(exc, FetchError):
            raise
        raise FetchError(f"AKShare真实行情采集或验证失败: {exc}; 上游错误: {errors}") from exc

def fetch_sse_shares(etf_code: str, trade_dates: list[date], pause_seconds: float = 0.05) -> tuple[list[ShareRecord], list[str]]:
    """从上交所公开ETF规模接口读取份额，TOT_VOL口径为万份。"""
    url = "https://query.sse.com.cn/commonQuery.do"
    headers = {"Referer": "https://www.sse.com.cn/", "User-Agent": "Mozilla/5.0 ETFResearchAgent/0.1"}
    rows, warnings = [], []
    fetched_at = datetime.utcnow()
    for trade_date in sorted(set(trade_dates)):
        params = {
            "isPagination": "true", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
            "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "STAT_DATE": trade_date.isoformat(),
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            result = response.json().get("result")
            if not isinstance(result, list):
                raise ValueError("result字段不是列表")
            match = next((item for item in result if str(item.get("SEC_CODE")) == etf_code), None)
            if not match:
                warnings.append(f"{trade_date}: 上交所尚未发布或未找到份额")
                continue
            if not {"SEC_CODE", "STAT_DATE", "TOT_VOL"}.issubset(match):
                raise ValueError("上交所份额字段发生变化")
            shares = float(match["TOT_VOL"]) * 10000
            if shares <= 0:
                raise ValueError("份额不是正数")
            rows.append(ShareRecord(etf_code=etf_code, trade_date=date.fromisoformat(match["STAT_DATE"]), shares=shares, source="sse:COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", fetched_at=fetched_at))
        except Exception as exc:
            warnings.append(f"{trade_date}: 份额采集失败: {exc}")
        if pause_seconds:
            time.sleep(pause_seconds)
    return rows, warnings

