from datetime import date, datetime, timedelta
import math
import random
import time
import requests
from app.config import ETF_CATALOG, REQUEST_TIMEOUT
from app.models.research import Document, ETFProfile, MarketBar, ShareRecord

class FetchError(RuntimeError):
    pass


# 演示数据只模拟交易日。2026年日期依据上交所公布的年度休市安排；
# 其他年份仍仅排除周末，不能把合成日历用于真实模式。
SSE_2026_CLOSED = (
    (date(2026, 1, 1), date(2026, 1, 3)),
    (date(2026, 2, 15), date(2026, 2, 23)),
    (date(2026, 4, 4), date(2026, 4, 6)),
    (date(2026, 5, 1), date(2026, 5, 5)),
    (date(2026, 6, 19), date(2026, 6, 21)),
    (date(2026, 9, 25), date(2026, 9, 27)),
    (date(2026, 10, 1), date(2026, 10, 7)),
)


def demo_trading_day(day: date) -> bool:
    return day.weekday() < 5 and not (day.year == 2026 and any(start <= day <= end for start, end in SSE_2026_CLOSED))

def catalog_profile(etf_code: str) -> ETFProfile:
    return ETFProfile(etf_code=etf_code, **ETF_CATALOG[etf_code], source="sse_fund_list")

def demo_market(end_date: date, days: int = 90, etf_code: str = "510300") -> list[MarketBar]:
    rng = random.Random(int(etf_code))
    rows, price = [], {"510300": 3.85, "510050": 2.75, "510500": 5.8}[etf_code]
    for i in range(days):
        d = end_date - timedelta(days=days - i - 1)
        if not demo_trading_day(d):
            continue
        price *= 1 + 0.0015 * math.sin(i / 4) + rng.uniform(-0.012, 0.012)
        turnover = 1.2e9 * (1 + 0.25 * math.sin(i / 7) + rng.uniform(-0.1, 0.1))
        rows.append(MarketBar(etf_code=etf_code, trade_date=d, close=round(price, 4), volume=turnover / price, turnover=round(turnover, 2), source="demo_synthetic", fetched_at=datetime.utcnow()))
    return rows

def demo_shares(end_date: date, days: int = 90, etf_code: str = "510300") -> list[ShareRecord]:
    rows = []
    for i in range(days):
        d = end_date - timedelta(days=days - i - 1)
        if not demo_trading_day(d):
            continue
        rows.append(ShareRecord(etf_code=etf_code, trade_date=d, shares=(0.85e11 + int(etf_code[-3:]) * 1e8) + i * 1.8e8, source="demo_synthetic", fetched_at=datetime.utcnow()))
    return rows

def demo_documents(as_of: date, etf_code: str = "510300") -> list[Document]:
    published_at = as_of - timedelta(days=30)
    name = ETF_CATALOG[etf_code]["name"]
    return [Document(document_id=f"demo-{etf_code}-report-{published_at}", title=f"{name}定期报告（演示资料）", content=f"本文件为合成演示资料，不代表真实公告。{etf_code}{name}的示例报告提及基金规模、份额和跟踪指数。投资者应关注数据日期与披露口径。", source_url=f"https://example.com/demo/{etf_code}-report", publisher="演示资料库", published_at=published_at, fetched_at=datetime.utcnow(), document_type="fund_report", etf_codes=[etf_code])]

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
            missing = {"日期", "收盘", "成交量", "成交额"}.difference(data.columns)
            if missing:
                errors.append(f"eastmoney:字段变化，缺少 {sorted(missing)}")
                data = None
        if data is not None and not data.empty:
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
            if not all(math.isfinite(value) for value in (close, volume, turnover)) or close <= 0 or volume < 0 or turnover < 0:
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
            returned_date = date.fromisoformat(match["STAT_DATE"])
            if returned_date != trade_date:
                warnings.append(f"{trade_date}: 上交所返回了其他日期的份额 {returned_date}，已忽略")
                continue
            shares = float(match["TOT_VOL"]) * 10000
            if shares <= 0:
                raise ValueError("份额不是正数")
            rows.append(ShareRecord(etf_code=etf_code, trade_date=returned_date, shares=shares, source="sse:COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", fetched_at=fetched_at))
        except Exception as exc:
            warnings.append(f"{trade_date}: 份额采集失败: {exc}")
        if pause_seconds:
            time.sleep(pause_seconds)
    return rows, warnings

