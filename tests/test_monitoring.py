from datetime import date, datetime
import json
import re
from pathlib import Path

from app.analysis.indicators import price_return, volume_ratio
from app.analysis.signals import calculate_signal
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.models.research import MarketBar, MonitorRun, ShareRecord
from app.monitoring import service


AS_OF = date(2026, 9, 30)


def test_five_indicators_explain_deterministic_signal():
    bars = [MarketBar(etf_code="510300", trade_date=date(2026, 9, day), close=3 + day / 100,
        volume=100 if day <= 10 else 150, turnover=1000 + day * 50,
        source="test", fetched_at=datetime(2026, 9, 30)) for day in range(1, 16)]
    shares = [ShareRecord(etf_code="510300", trade_date=date(2026, 9, day),
        shares=1000 + day * 10, source="test", fetched_at=datetime(2026, 9, 30))
        for day in range(1, 16)]
    result = calculate_signal("510300", date(2026, 9, 15), bars, shares, "live")
    assert len(result.indicators) == len(result.trigger_details) == 5
    assert price_return(bars) > 0 and volume_ratio(bars) > 1
    assert result.signal == "positive"
    assert result.algorithm_version == "v0.2"
    assert all(row["reason"] for row in result.trigger_details)


def test_volume_ratio_accepts_only_normalized_live_source_mix():
    bars = [MarketBar(etf_code="510050", trade_date=date(2026, 9, day), close=3.0,
        volume=100 if day <= 5 else 150, turnover=1000,
        source="akshare:eastmoney" if day <= 8 else "akshare:sina",
        fetched_at=datetime(2026, 9, 30)) for day in range(1, 11)]
    assert volume_ratio(bars) == 1.5
    assert volume_ratio([*bars[:9], bars[-1].model_copy(update={"source": "unknown"})]) is None


def test_history_uses_only_records_available_on_each_date(tmp_path):
    repo = SQLiteRepository(tmp_path / "history.sqlite3")
    seed_demo(repo, AS_OF)
    repo.upsert_shares([ShareRecord(etf_code="510300", trade_date=date(2026, 10, 8),
        shares=1e15, source="future_test", fetched_at=datetime(2026, 10, 8))])
    history = service.backfill_signal_history(repo, "510300", AS_OF, "demo")
    assert history and history[-1].as_of_date == AS_OF
    assert all(all(source_date <= item.as_of_date for source_date in item.source_dates.values()) for item in history)
    assert history[-1].latest_values["shares"] != 1e15
    stored = repo.signal_history("510300", date(2026, 1, 1), AS_OF, "v0.2")
    assert len(stored) == len(history)
    repo.close()


def test_comparison_warns_when_source_dates_differ(tmp_path):
    repo = SQLiteRepository(tmp_path / "compare.sqlite3")
    seed_demo(repo, AS_OF)
    repo.conn.execute("DELETE FROM etf_daily WHERE etf_code='510050' AND trade_date=?", (AS_OF.isoformat(),))
    repo.conn.commit()
    comparison = service.compare_etfs(repo, ["510300", "510050"], AS_OF, "demo")
    assert len(comparison["rows"]) == 2
    assert any("日期不同" in warning for warning in comparison["warnings"])
    repo.close()


def test_monitor_alert_is_idempotent_and_manual_mode_never_syncs(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "monitor.sqlite3")
    seed_demo(repo, AS_OF)
    latest = repo.market("510300", AS_OF, limit=1)[-1]
    repo.upsert_market([latest.model_copy(update={"turnover": latest.turnover * 100})])
    monkeypatch.setattr(service, "sync_market_data", lambda *args: (_ for _ in ()).throw(AssertionError("network sync called")))
    first = service.run_monitor_once(repo, AS_OF, "demo", sync_live=False)
    second = service.run_monitor_once(repo, AS_OF, "demo", sync_live=False)
    assert first.status == second.status == "success"
    alerts = repo.recent_monitor_alerts()
    assert sum(item.category == "turnover_spike" and item.etf_code == "510300" for item in alerts) == 1
    assert len(repo.signal_history("510300", date(2026, 1, 1), AS_OF, "v0.2")) > 0
    repo.close()


def test_known_market_holiday_skips_live_sync(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "holiday.sqlite3")
    seed_demo(repo, AS_OF)
    monkeypatch.setattr(service, "sync_market_data", lambda *args: (_ for _ in ()).throw(AssertionError("holiday must not sync")))
    result = service.run_monitor_once(repo, date(2026, 10, 7), "live", sync_live=True)
    assert all(row["sync_status"] == "market_closed" for row in result.etf_results)
    repo.close()


def test_stale_market_is_labeled_after_holiday(tmp_path):
    repo = SQLiteRepository(tmp_path / "stale.sqlite3")
    seed_demo(repo, AS_OF)
    result = service.run_monitor_once(repo, date(2026, 10, 8), "demo", sync_live=False)
    assert result.status == "success"
    assert {alert.etf_code for alert in repo.recent_monitor_alerts() if alert.category == "stale_market"} == {
        "510300", "510050", "510500"}
    repo.close()


def _scheduled_monitor_result(day, complete=True, run_id="monitor-1"):
    rows = []
    for code in ("510300", "510050", "510500"):
        rows.append({"etf_code": code, "status": "success", "sync_status": "success",
            "share_sync_status": "success", "latest_market_date": day.isoformat(),
            "latest_share_date": (day if complete else day.replace(day=day.day - 1)).isoformat()})
    return MonitorRun(run_id=run_id, as_of_date=day, started_at=datetime(2026, 10, 8, 18, 30),
        finished_at=datetime(2026, 10, 8, 18, 31), status="success", etf_results=rows)


def test_scheduled_primary_is_idempotent_and_complete_retry_skips(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "scheduled.sqlite3")
    day = date(2026, 10, 8)
    calls = []
    monkeypatch.setattr(service, "run_monitor_once",
        lambda *args, **kwargs: calls.append("run") or _scheduled_monitor_result(day))

    first = service.run_scheduled_monitor(repo, day, "primary")
    repeated = service.run_scheduled_monitor(repo, day, "primary")
    retry = service.run_scheduled_monitor(repo, day, "retry")

    assert first.status == repeated.status == "success"
    assert retry.status == "skipped"
    assert calls == ["run"]
    assert len(repo.recent_scheduled_runs()) == 2
    repo.close()


def test_scheduled_retry_runs_once_when_primary_data_is_incomplete(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "scheduled.sqlite3")
    day = date(2026, 10, 8)
    results = iter([
        _scheduled_monitor_result(day, complete=False, run_id="monitor-primary"),
        _scheduled_monitor_result(day, complete=True, run_id="monitor-retry"),
    ])
    monkeypatch.setattr(service, "run_monitor_once", lambda *args, **kwargs: next(results))

    primary = service.run_scheduled_monitor(repo, day, "primary")
    retry = service.run_scheduled_monitor(repo, day, "retry")
    repeated = service.run_scheduled_monitor(repo, day, "retry")

    assert primary.status == "partial"
    assert retry.status == repeated.status == "success"
    assert retry.monitor_run_id == "monitor-retry"
    repo.close()


def test_scheduled_monitor_skips_known_holiday_without_network(tmp_path, monkeypatch):
    repo = SQLiteRepository(tmp_path / "scheduled.sqlite3")
    monkeypatch.setattr(service, "run_monitor_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("holiday must not run")))
    result = service.run_scheduled_monitor(repo, date(2026, 10, 7), "primary")
    assert result.status == "skipped"
    assert "休市日" in result.reason
    repo.close()


def test_morning_start_when_available_targets_previous_known_trading_day():
    assert service.scheduled_target_date(datetime(2026, 10, 8, 8, 30)) == date(2026, 9, 30)
    assert service.scheduled_target_date(datetime(2026, 10, 8, 18, 30)) == date(2026, 10, 8)


def test_page_tables_and_detail_keys_are_readable_chinese(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import app.config as config

    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "etf_research.sqlite3")
    monkeypatch.setattr(config, "DATA_MODE", "demo")
    monkeypatch.setattr(config, "SETTINGS_DATABASE_PATH", tmp_path / "app_settings.sqlite3")
    repo = SQLiteRepository(config.database_path_for_mode("demo"))
    seed_demo(repo, AS_OF)
    repo.close()
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] /
        "app" / "frontend" / "streamlit_app.py")).run()
    next(button for button in page.button if button.label == "生成研究简报").click().run()
    next(button for button in page.button if button.label == "检查三只基金的演示数据（不联网）").click().run()
    assert not page.exception, [item.message for item in page.exception]
    frames = [item.value for item in page.dataframe]
    def frame_with(*required):
        return next(frame for frame in frames if set(required) <= set(frame.columns))
    indicators = frame_with("指标", "结果", "对信号的作用", "如何理解")
    monitor = frame_with("基金", "检查结果", "行情更新", "最新观察信号")
    comparison = frame_with("基金代码", "基金名称", "观察信号", "近60日价格位置")
    frame_with("观察日期", "支持偏积极的指标", "缺少的指标")
    assert indicators.iloc[0]["结果"].endswith("%")
    assert monitor.iloc[0]["检查结果"] == "已完成"
    assert comparison.iloc[0]["近60日价格位置"].endswith("%")

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    for element in page.json:
        payload = json.loads(element.value) if isinstance(element.value, str) else element.value
        assert not [key for key in keys(payload) if re.search(r"[A-Za-z_]", str(key))]
