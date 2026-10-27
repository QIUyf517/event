"""创建第三阶段独立数据库，并采集带日期的公司历史行情。"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from app.data.repositories.sqlite import SQLiteRepository


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output/event-phase2-20261012-accepted/event_research_phase2.sqlite3"
TARGET_DIR = ROOT / "output/event-phase3-20261012-acceptance"
TARGET = TARGET_DIR / "event_research_phase3.sqlite3"
START, END = date(2026, 8, 14), date(2026, 9, 13)
SUBJECTS = ("600519", "601318", "688981")


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError(f"缺少已验收的阶段二快照：{SOURCE}")
    if TARGET.exists():
        raise FileExistsError(f"验收库已存在，拒绝覆盖：{TARGET}")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, TARGET)
    import akshare as ak

    repo = SQLiteRepository(TARGET)
    counts = {}
    try:
        for code in SUBJECTS:
            frame = ak.stock_zh_a_hist(symbol=code, period="daily",
                start_date=START.strftime("%Y%m%d"), end_date=END.strftime("%Y%m%d"),
                adjust="", timeout=15)
            if frame is None or frame.empty:
                raise RuntimeError(f"{code} 无历史行情")
            rows = []
            for _, item in frame.iterrows():
                day = date.fromisoformat(str(item["日期"])[:10])
                if not START <= day <= END:
                    continue
                rows.append({"subject_id": code, "trade_date": day.isoformat(),
                    "close": float(item["收盘"]), "volume": float(item["成交量"]),
                    "turnover": float(item["成交额"]), "source": "eastmoney_via_akshare",
                    "source_url": f"https://quote.eastmoney.com/{'sh' if code.startswith('6') else 'sz'}{code}.html",
                    "fetched_at": datetime.now(timezone.utc).isoformat()})
            if not rows:
                raise RuntimeError(f"{code} 范围内无行情记录")
            repo.upsert_company_market(rows)
            counts[code] = {"rows": len(rows), "first": min(r["trade_date"] for r in rows),
                            "last": max(r["trade_date"] for r in rows)}
    finally:
        repo.close()
    (TARGET_DIR / "market_collection.json").write_text(json.dumps({
        "source_database": str(SOURCE), "database": str(TARGET), "subjects": counts,
        "data_source": "Eastmoney historical daily unadjusted via AkShare stock_zh_a_hist",
        "window_start": START.isoformat(), "as_of_date": END.isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
