"""网页检索必须使用第二阶段语料，且不得修改该语料。"""

from datetime import date

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.rag.ingest import build_document


def make_corpus(path):
    repo = SQLiteRepository(path)
    seed_event_research_subjects(repo)
    for subject, kind, day, word in (
        ("600519", "financial", date(2026, 8, 15), "营业收入"),
        ("600519", "news", date(2026, 8, 20), "营业收入"),
        ("600519", "announcement", date(2026, 8, 23), "营业收入"),
        ("600519", "news", date(2026, 9, 2), "营业收入"),
        ("601318", "financial", date(2026, 8, 21), "营业收入"),
    ):
        url = f"https://www.stcn.com/article/{subject}-{kind}-{day}.html"
        payload = f"<html><article><p>{subject}的{kind}{word}资料内容，发布于{day}。</p></article></html>".encode()
        document, chunks = build_document(payload, "material.html", f"{subject}{kind}{day}",
            "测试发布者", day, url, kind, subject_id=subject, source_id="stcn_news")
        repo.store_event_document(document, chunks)
    repo.close()


def test_web_search_filters_subject_dates_and_material_type_without_a_write_key(tmp_path):
    path = tmp_path / "events.sqlite3"
    make_corpus(path)
    with TestClient(create_app(event_database_path=path, write_key="test-secret")) as client:
        catalog = client.get("/api/v1/event-research/subjects")
        assert catalog.status_code == 200
        assert {item["subject_id"] for item in catalog.json()["subjects"]} == {
            "600519", "601318", "688981"}
        assert catalog.json()["available_start"] == "2026-08-15"
        assert catalog.json()["available_end"] == "2026-09-02"

        response = client.get("/api/v1/event-research/search", params={
            "subject_id": "600519", "start_date": "2026-08-14",
            "end_date": "2026-08-31", "query": "营业收入",
            "source_types": "financial"})
        assert response.status_code == 200, response.text
        assert response.json()["count"] == 1
        hit = response.json()["items"][0]
        assert hit["document_type"] == "financial"
        assert hit["publisher"] == "测试发布者"
        assert hit["published_at"] == "2026-08-15"
        assert "营业收入" in hit["excerpt"]
        assert hit["source_url"].startswith("https://")

        all_types = client.get("/api/v1/event-research/search", params={
            "subject_id": "600519", "start_date": "2026-08-14",
            "end_date": "2026-08-31", "query": "营业收入"})
        assert all_types.status_code == 200
        assert {item["document_type"] for item in all_types.json()["items"]} == {
            "announcement", "financial", "news"}
        assert all(item["published_at"] <= "2026-08-31" for item in all_types.json()["items"])
        assert all("600519" in item["title"] for item in all_types.json()["items"])

        assert client.get("/api/v1/event-research/search", params={
            "subject_id": "600519", "start_date": "2026-08-31",
            "end_date": "2026-08-14", "query": "营业收入"}).status_code == 422
        assert client.get("/api/v1/event-research/search", params={
            "subject_id": "000000", "start_date": "2026-08-14",
            "end_date": "2026-08-31", "query": "营业收入"}).status_code == 422


def test_web_search_does_not_create_a_missing_event_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with TestClient(create_app(event_database_path=missing)) as client:
        response = client.get("/api/v1/event-research/subjects")
    assert response.status_code == 404
    assert not missing.exists()
