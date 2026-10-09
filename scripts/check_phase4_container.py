"""Check the running Compose API before and after a container restart."""

import hashlib
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = "http://127.0.0.1:8000"
OUT = Path("output/phase4_container_acceptance")
STATE = OUT / "state.json"


def request(path: str, method: str = "GET", payload: dict | None = None, key: str | None = None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30) as response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def load_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ETF_API_WRITE_KEY="):
            return line.partition("=")[2]
    raise RuntimeError("ETF_API_WRITE_KEY missing from isolated test .env")


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main(stage: str):
    OUT.mkdir(parents=True, exist_ok=True)
    key = load_key()
    health = json.loads(request("/health")[1])
    assert health["status"] == "ok" and health["write_api_configured"]
    spec = json.loads(request("/openapi.json")[1])
    assert "/api/v1/research" in spec["paths"] and "/api/v1/reports/{task_id}/pdf" in spec["paths"]
    etfs = json.loads(request("/api/v1/etfs")[1])
    assert {item["etf_code"] for item in etfs} == {"510300", "510050", "510500"}

    if stage == "before":
        try:
            request("/api/v1/research", "POST", {"question": "分析当前资金信号", "etf_code": "510300", "as_of_date": "2026-09-30", "mode": "demo"})
        except HTTPError as exc:
            assert exc.code == 401, f"missing key returned {exc.code}"
        else:
            raise AssertionError("research write endpoint accepted a missing API key")
        status, data, _ = request("/api/v1/research", "POST", {
            "question": "分析当前资金信号，并结合已入库公开资料说明证据和局限",
            "etf_code": "510300", "as_of_date": "2026-09-30", "mode": "demo",
        }, key)
        assert status == 200
        created = json.loads(data)
        task_id = created["task_id"]
        report_hash = digest(created["report"])
        STATE.write_text(json.dumps({"task_id": task_id, "report_hash": report_hash}), encoding="utf-8")
    else:
        saved = json.loads(STATE.read_text(encoding="utf-8"))
        task_id, report_hash = saved["task_id"], saved["report_hash"]

    stored = json.loads(request(f"/api/v1/reports/{task_id}?mode=demo")[1])
    assert digest(stored["report"]) == report_hash, "archived report changed after restart"
    listing = json.loads(request("/api/v1/reports?mode=demo&limit=10")[1])
    assert any(item["task_id"] == task_id for item in listing["items"])
    pdf_status, pdf, pdf_type = request(f"/api/v1/reports/{task_id}/pdf?mode=demo")
    assert pdf_status == 200 and "application/pdf" in pdf_type and pdf.startswith(b"%PDF-") and len(pdf) > 1000
    (OUT / f"report_{stage}.pdf").write_bytes(pdf)
    summary = {
        "stage": stage, "task_id": task_id, "report_hash": report_hash,
        "pdf_bytes": len(pdf), "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "health": health["status"], "demo_db_exists": health["databases"]["demo"]["exists"],
        "archive_count": len(listing["items"]), "missing_key_rejected": stage == "before",
    }
    (OUT / f"result_{stage}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"before", "after"}:
        raise SystemExit("usage: check_phase4_container.py before|after")
    main(sys.argv[1])
