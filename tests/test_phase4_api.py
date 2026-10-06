from datetime import date
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api.main import create_app


def client_for(tmp_path):
    def resolver(mode):
        return tmp_path / f"etf_research.{mode}.sqlite3"
    return TestClient(create_app(resolver, write_key="test-secret",
                                 settings_path=tmp_path / "app_settings.sqlite3"))


def test_health_and_read_endpoints_do_not_require_key(tmp_path):
    with client_for(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/openapi.json").status_code == 200
        etfs = client.get("/api/v1/etfs")
        assert etfs.status_code == 200
        assert {item["etf_code"] for item in etfs.json()} == {"510300", "510050", "510500"}


def test_write_endpoints_require_api_key(tmp_path):
    with client_for(tmp_path) as client:
        payload = {"question": "分析当前资金信号", "etf_code": "510300",
                   "as_of_date": "2026-09-30", "mode": "demo"}
        assert client.post("/api/v1/research", json=payload).status_code == 401
        assert client.post("/api/v1/research", json=payload,
            headers={"X-API-Key": "wrong"}).status_code == 401


def test_research_archive_and_pdf_round_trip(tmp_path):
    with client_for(tmp_path) as client:
        payload = {"question": "分析当前资金信号", "etf_code": "510300",
                   "as_of_date": "2026-09-30", "mode": "demo"}
        created = client.post("/api/v1/research", json=payload,
            headers={"X-API-Key": "test-secret"})
        assert created.status_code == 200, created.text
        task_id = created.json()["task_id"]

        archive = client.get("/api/v1/reports", params={"mode": "demo"})
        assert archive.status_code == 200
        assert archive.json()["items"][0]["task_id"] == task_id

        report = client.get(f"/api/v1/reports/{task_id}", params={"mode": "demo"})
        assert report.status_code == 200
        assert report.json()["report"]["etf"]["etf_code"] == "510300"

        pdf = client.get(f"/api/v1/reports/{task_id}/pdf", params={"mode": "demo"})
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")
        reader = PdfReader(BytesIO(pdf.content))
        assert len(reader.pages) >= 1
        assert "ETF" in (reader.metadata.title or "")


def test_settings_and_monitor_logs_are_persisted(tmp_path):
    with client_for(tmp_path) as client:
        settings = {"default_etf": "510500", "default_mode": "demo",
                    "default_question": "查看成交额热度", "report_page_size": 15}
        saved = client.put("/api/v1/settings", json=settings,
            headers={"X-API-Key": "test-secret"})
        assert saved.status_code == 200
        assert saved.json()["default_etf"] == "510500"
        assert saved.json()["updated_at"]
        assert client.get("/api/v1/settings").json()["report_page_size"] == 15

        monitored = client.post("/api/v1/monitor/run", json={"as_of_date": "2026-09-30",
            "mode": "demo", "sync_live": False}, headers={"X-API-Key": "test-secret"})
        assert monitored.status_code == 200
        runs = client.get("/api/v1/monitor/runs", params={"mode": "demo"})
        assert runs.status_code == 200
        assert runs.json()["items"][0]["as_of_date"] == "2026-09-30"


def test_missing_write_key_fails_closed(tmp_path):
    def resolver(mode):
        return tmp_path / f"{mode}.sqlite3"
    with TestClient(create_app(resolver, write_key="", settings_path=tmp_path / "settings.sqlite3")) as client:
        response = client.post("/api/v1/monitor/run", json={"mode": "demo"})
        assert response.status_code == 503
