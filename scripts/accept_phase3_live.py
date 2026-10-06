"""在真实数据库的临时副本上验收第三阶段的手动监控。"""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

from app.config import ALGORITHM_VERSION, ALLOWED_ETFS, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import UserSettings
from app.monitoring.service import compare_etfs, run_monitor_once
from scripts.accept_phase2_live import snapshot_live_database


def check_manual_button(snapshot: Path) -> str:
    import app.config as config
    from streamlit.testing.v1 import AppTest

    config.DATABASE_PATH = snapshot.with_name("etf_research.sqlite3")
    config.SETTINGS_DATABASE_PATH = snapshot.with_name("app_settings.sqlite3")
    config.DATA_MODE = "live"
    settings_repo = SQLiteRepository(config.SETTINGS_DATABASE_PATH)
    settings_repo.save_user_settings(UserSettings(default_mode="live"))
    settings_repo.close()
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] /
        "app" / "frontend" / "streamlit_app.py")).run()
    assert not page.exception, [item.message for item in page.exception]
    next(button for button in page.button if button.label == "手动更新并检查三只基金").click().run()
    assert not page.exception, [item.message for item in page.exception]
    assert any("上次手动检查" in item.value for item in [*page.markdown, *page.caption])
    return "passed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    source = database_path_for_mode("live")
    if not source.is_file():
        parser.error(f"实时数据库不存在: {source}")
    with tempfile.TemporaryDirectory(prefix="etf-phase3-accept-") as temp_dir:
        snapshot = Path(temp_dir) / "etf_research.live.sqlite3"
        snapshot_live_database(source, snapshot)
        repo = SQLiteRepository(snapshot)
        try:
            # 不联网；只验收真实来源数据上的回填、异常检测、对比和落库。
            prior_runs = repo.conn.execute("SELECT COUNT(*) FROM monitor_runs").fetchone()[0]
            run = run_monitor_once(repo, args.as_of, "live", sync_live=False)
            assert run.status != "failed", run.warnings
            assert len(run.etf_results) == len(ALLOWED_ETFS)
            assert all(item["status"] == "success" and item["history_rows"] > 0
                       for item in run.etf_results), run.etf_results
            history_counts = {code: len(repo.signal_history(code, date(2020, 1, 1),
                args.as_of, ALGORITHM_VERSION)) for code in ALLOWED_ETFS}
            assert all(count > 0 for count in history_counts.values())
            comparison = compare_etfs(repo, list(ALLOWED_ETFS), args.as_of, "live")
            assert len(comparison["rows"]) == len(ALLOWED_ETFS)
            assert all(len(row["trigger_details"]) == 5 for row in comparison["rows"])
            saved_run = repo.conn.execute("SELECT COUNT(*) FROM monitor_runs").fetchone()[0]
            assert saved_run == prior_runs + 1
            result = {"as_of_date": args.as_of.isoformat(),
                "database_access": "original_read_only; temporary_snapshot_read_write; no_network",
                "run_status": run.status, "etf_results": run.etf_results,
                "history_counts": history_counts, "comparison_warnings": comparison["warnings"],
                "saved_runs": saved_run, "alert_count": len(repo.recent_monitor_alerts())}
        finally:
            repo.close()
        result["manual_button"] = check_manual_button(snapshot)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
