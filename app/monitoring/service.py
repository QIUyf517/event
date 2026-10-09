"""历史信号、数据异常、跨 ETF 对比及一次性监控。"""

import math
from datetime import date, datetime, timedelta
from statistics import median
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.analysis.signals import calculate_signal
from app.config import ALGORITHM_VERSION, ALLOWED_ETFS
from app.data.fetchers import SSE_2026_CLOSED
from app.data.repositories.sqlite import SQLiteRepository
from app.data.sync import sync_market_data
from app.models.research import MonitorAlert, MonitorRun, SignalResult

SIGNAL_LABELS = {"positive": "偏积极", "moderately_positive": "温和积极", "neutral": "中性",
                 "cautious": "偏谨慎", "insufficient_data": "数据不足"}


def shanghai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def known_market_closed(day: date) -> bool:
    return day.weekday() >= 5 or (day.year == 2026 and any(start <= day <= end for start, end in SSE_2026_CLOSED))


def backfill_signal_history(repo: SQLiteRepository, etf_code: str, as_of_date: date,
                            data_mode: str = "live", limit: int = 252) -> list[SignalResult]:
    # 最早待回填日也要有完整的 252 条成交额分位历史。
    bars = repo.market(etf_code, as_of_date, limit=limit + 252)
    shares = repo.shares(etf_code, as_of_date, limit=limit + 5)
    results = []
    for bar in bars[-limit:]:
        history_bars = [item for item in bars if item.trade_date <= bar.trade_date]
        history_shares = [item for item in shares if item.trade_date <= bar.trade_date]
        results.append(calculate_signal(etf_code, bar.trade_date, history_bars, history_shares, data_mode))
    repo.save_signals(results)
    return results


def compare_etfs(repo: SQLiteRepository, etf_codes: list[str], as_of_date: date,
                 data_mode: str = "live") -> dict:
    if not etf_codes or any(code not in ALLOWED_ETFS for code in etf_codes):
        raise ValueError("ETF 对比至少需要一只受支持的 ETF")
    rows = []
    for code in dict.fromkeys(etf_codes):
        signal = calculate_signal(code, as_of_date, repo.market(code, as_of_date),
                                  repo.shares(code, as_of_date), data_mode)
        rows.append({"etf_code": code, "name": ALLOWED_ETFS[code],
                     "signal": signal.signal, "evidence_strength": signal.evidence_strength,
                     "indicators": signal.indicators, "source_dates": signal.model_dump(mode="json")["source_dates"],
                     "missing_fields": signal.missing_fields, "trigger_details": signal.trigger_details})
    market_dates = {row["source_dates"].get("market") for row in rows}
    warnings = []
    if len(market_dates) > 1:
        warnings.append("各 ETF 行情截止日期不同，不应直接按信号强弱排序。")
    if any(row["missing_fields"] for row in rows):
        warnings.append("部分 ETF 的五项指标不完整，不能形成完整对比。")
    return {"as_of_date": as_of_date.isoformat(), "data_mode": data_mode,
            "rows": rows, "warnings": warnings}


def _alert(etf_code: str, day: date, category: str, severity: str, title: str, message: str) -> MonitorAlert:
    return MonitorAlert(alert_key=f"{etf_code}:{day.isoformat()}:{category}", etf_code=etf_code,
        as_of_date=day, category=category, severity=severity, title=title, message=message,
        created_at=shanghai_now())


def detect_data_anomalies(repo: SQLiteRepository, etf_code: str, as_of_date: date) -> list[MonitorAlert]:
    bars = repo.market(etf_code, as_of_date, limit=30)
    shares = repo.shares(etf_code, as_of_date, limit=10)
    alerts = []
    if not bars:
        return [_alert(etf_code, as_of_date, "missing_market", "error", "缺少行情数据", "截至查询日没有可用行情记录。")]
    latest = bars[-1]
    values = (latest.close, latest.volume, latest.turnover)
    if (as_of_date - latest.trade_date).days > 5 and not known_market_closed(as_of_date):
        alerts.append(_alert(etf_code, as_of_date, "stale_market", "warning",
            "行情数据未更新", f"查询日期为 {as_of_date}，最新行情仍截至 {latest.trade_date}；本次信号不代表查询日新行情。"))
    if any(value is None or not math.isfinite(value) or value <= 0 for value in values):
        alerts.append(_alert(etf_code, latest.trade_date, "invalid_market", "error",
            "行情数值异常", "最新收盘价、成交量或成交额存在缺失或非正数；请核查原始来源。"))
    if len(bars) >= 2 and latest.close and bars[-2].close:
        change = latest.close / bars[-2].close - 1
        if abs(change) >= 0.15:
            alerts.append(_alert(etf_code, latest.trade_date, "large_price_move", "warning",
                "单日价格变动较大", f"最新相邻可用收盘价变化 {change:+.1%}；可能涉及行情异常或除权，需人工核对。"))
    previous_turnovers = [bar.turnover for bar in bars[-21:-1] if bar.turnover and bar.turnover > 0]
    if len(previous_turnovers) >= 10 and latest.turnover and latest.turnover > 10 * median(previous_turnovers):
        alerts.append(_alert(etf_code, latest.trade_date, "turnover_spike", "warning",
            "成交额突增", "最新成交额超过前20个可用样本中位数的10倍，请核对来源口径。"))
    if len(shares) >= 2 and shares[-1].shares and shares[-2].shares:
        change = shares[-1].shares / shares[-2].shares - 1
        if abs(change) >= 0.2:
            alerts.append(_alert(etf_code, shares[-1].trade_date, "large_share_change", "warning",
                "份额披露变化较大", f"最近两次可用披露份额变化 {change:+.1%}；可能涉及申赎或口径调整。"))
    if shares and (latest.trade_date - shares[-1].trade_date).days > 10:
        alerts.append(_alert(etf_code, as_of_date, "shares_lag", "warning",
            "份额披露滞后", f"份额截至 {shares[-1].trade_date}，行情截至 {latest.trade_date}。"))
    return alerts


def run_monitor_once(repo: SQLiteRepository, as_of_date: date, data_mode: str = "live",
                     sync_live: bool = True) -> MonitorRun:
    if sync_live and data_mode != "live":
        raise ValueError("只有实时模式可以联网同步")
    started = shanghai_now()
    etf_results, warnings, alerts = [], [], []
    for code in ALLOWED_ETFS:
        sync_status, share_sync_status = "not_requested", "not_requested"
        try:
            prior_bars = repo.market(code, as_of_date, limit=1)
            if sync_live and known_market_closed(as_of_date):
                sync_status = "market_closed"
            elif sync_live:
                already_current = bool(prior_bars and prior_bars[-1].trade_date >= as_of_date)
                start = as_of_date if already_current else (
                    prior_bars[-1].trade_date + timedelta(days=1) if prior_bars else as_of_date - timedelta(days=120))
                sync = sync_market_data(repo, code, start, as_of_date)
                sync_status = "already_current" if already_current and sync.market_status == "success" else sync.market_status
                share_sync_status = sync.share_status
                warnings.extend(f"{code}: {item}" for item in sync.warnings)
                if sync.market_status == "failed":
                    alerts.append(_alert(code, as_of_date, "sync_failed", "warning",
                        "行情同步未成功", "本次没有取得新的有效行情；请核对来源和交易日，不会混入演示数据。"))
            history = backfill_signal_history(repo, code, as_of_date, data_mode)
            alerts.extend(detect_data_anomalies(repo, code, as_of_date))
            valid = [item for item in history if item.signal != "insufficient_data"]
            if len(valid) >= 2 and history[-1] == valid[-1] and valid[-1].signal != valid[-2].signal:
                alerts.append(_alert(code, valid[-1].as_of_date, "signal_change", "info",
                    "资金观察信号发生变化", f"信号由“{SIGNAL_LABELS[valid[-2].signal]}”变为“{SIGNAL_LABELS[valid[-1].signal]}”；请查看五项指标和来源日期。"))
            latest = history[-1] if history else None
            latest_shares = repo.shares(code, as_of_date, limit=1)
            etf_results.append({"etf_code": code, "status": "success" if latest else "missing_data",
                "sync_status": sync_status, "share_sync_status": share_sync_status,
                "history_rows": len(history),
                "latest_signal": latest.signal if latest else None,
                "latest_market_date": latest.source_dates.get("market").isoformat() if latest and latest.source_dates.get("market") else None,
                "latest_share_date": latest_shares[-1].trade_date.isoformat() if latest_shares else None,
                "trigger_details": latest.trigger_details if latest else []})
        except Exception as exc:
            warnings.append(f"{code}: 监控失败: {exc}")
            etf_results.append({"etf_code": code, "status": "failed", "sync_status": sync_status,
                                "share_sync_status": share_sync_status, "error": str(exc)})
            alerts.append(_alert(code, as_of_date, "monitor_failed", "error", "ETF监控失败", str(exc)))
    repo.save_monitor_alerts(alerts)
    failures = sum(item["status"] == "failed" for item in etf_results)
    status = "failed" if failures == len(etf_results) else "partial" if failures or warnings else "success"
    result = MonitorRun(run_id=str(uuid4()), as_of_date=as_of_date, started_at=started,
        finished_at=shanghai_now(), status=status, etf_results=etf_results, warnings=warnings)
    repo.save_monitor_run(result)
    return result
