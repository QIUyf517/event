from __future__ import annotations

from datetime import date
from hashlib import sha256

import pytest

from app.data.event_sources import validate_source_url
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.rag.ingest import build_document, collect_event_document
from app.tools.research_tools import EventResearchTools


def add_material(repo, subject_id: str, source_id: str, source_url: str, title: str,
                 published_at: date, body: str, document_type: str | None = None,
                 reprint_of: str | None = None):
    source_type = document_type or ("announcement" if source_id == "sse_announcement" else "news")
    payload = f"<html><article><h1>{title}</h1><p>{body}</p></article></html>".encode("utf-8")
    document, chunks = build_document(payload, "material.html", title, "测试发布者", published_at,
        source_url, source_type, subject_id=subject_id, source_id=source_id, reprint_of=reprint_of)
    return document, repo.store_event_document(document, chunks)


@pytest.fixture()
def event_repo(tmp_path):
    repo = SQLiteRepository(tmp_path / "event.sqlite3")
    seed_event_research_subjects(repo)
    yield repo
    repo.close()


def test_three_subjects_have_traceable_announcement_and_news_samples(event_repo):
    for subject_id, name in (("600519", "贵州茅台"), ("601318", "中国平安"), ("688981", "中芯国际")):
        add_material(event_repo, subject_id, "sse_announcement",
            f"https://www.sse.com.cn/disclosure/{subject_id}.html", f"{name}公告", date(2026, 9, 20),
            f"{name}发布经营事项公告。")
        add_material(event_repo, subject_id, "stcn_news",
            f"https://www.stcn.com/article/{subject_id}.html", f"{name}新闻", date(2026, 9, 21),
            f"证券时报报道{name}经营事项。")
        counts = event_repo.event_material_counts(subject_id)
        assert counts == {"documents": 2, "chunks": 2, "metadata_aligned_chunks": 2}


def test_url_content_title_and_reprint_duplicates_are_not_independent_evidence(event_repo):
    canonical, inserted = add_material(event_repo, "600519", "sse_announcement",
        "https://www.sse.com.cn/disclosure/a.html", "贵州茅台经营公告", date(2026, 9, 20), "公告正文A")
    assert inserted.status == "inserted"
    _, same_url = add_material(event_repo, "600519", "sse_announcement",
        canonical.source_url, "另一标题", date(2026, 9, 21), "不同正文")
    assert (same_url.status, same_url.duplicate_reason) == ("duplicate", "canonical_url")
    _, same_content = add_material(event_repo, "600519", "stcn_news",
        "https://www.stcn.com/article/copy.html", canonical.title, canonical.published_at, "公告正文A")
    assert (same_content.status, same_content.duplicate_reason) == ("duplicate", "content_hash")
    _, same_title = add_material(event_repo, "600519", "stcn_news",
        "https://www.stcn.com/article/title.html", canonical.title, canonical.published_at, "完全不同的报道正文")
    assert (same_title.status, same_title.duplicate_reason) == ("duplicate", "title")
    _, reprint = add_material(event_repo, "600519", "stcn_news",
        "https://www.stcn.com/article/reprint.html", "转载报道", date(2026, 9, 22), "转载正文",
        reprint_of=canonical.document_id)
    assert (reprint.status, reprint.duplicate_reason) == ("duplicate", "reprint_of")
    assert event_repo.event_material_counts("600519")["documents"] == 1


def test_as_of_filter_blocks_future_material_and_read_only_tools_split_types(event_repo):
    add_material(event_repo, "688981", "sse_announcement",
        "https://www.sse.com.cn/disclosure/past.html", "产能公告", date(2026, 9, 20), "中芯国际产能安排")
    add_material(event_repo, "688981", "stcn_news",
        "https://www.stcn.com/article/future.html", "未来报道", date(2026, 10, 2), "中芯国际未来资料")
    tools = EventResearchTools(event_repo)
    found = tools.search_all_materials("中芯国际", "688981", date(2026, 9, 1), date(2026, 9, 30))
    assert [item.chunk.title for item in found] == ["产能公告"]
    assert tools.search_news("中芯国际", "688981", date(2026, 9, 1), date(2026, 9, 30)) == []
    with pytest.raises(ValueError, match="开始日期"):
        tools.search_all_materials("中芯国际", "688981", date(2026, 10, 1), date(2026, 9, 30))


def test_collection_preserves_raw_snapshot_and_hash(monkeypatch, event_repo, tmp_path):
    payload = "<html><article><p>贵州茅台真实资料测试正文</p></article></html>".encode("utf-8")

    class Response:
        content = payload
        headers = {"content-type": "text/html; charset=utf-8"}
        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.rag.ingest.requests.get", lambda *args, **kwargs: Response())
    document, stored = collect_event_document(event_repo,
        "https://www.sse.com.cn/disclosure/test.html", "sse_announcement",
        snapshot_dir=tmp_path / "snapshots", subject_id="600519", title="测试公告",
        publisher="上海证券交易所", published_at=date(2026, 9, 20))
    snapshot = tmp_path / "snapshots" / f"{sha256(payload).hexdigest()}.html"
    assert stored.status == "inserted"
    assert snapshot.read_bytes() == payload
    assert document.content_hash == sha256(payload).hexdigest()
    assert event_repo.event_document(document.document_id).snapshot_path == str(snapshot.resolve())


def test_source_policy_rejects_wrong_domain_and_synthetic_source():
    with pytest.raises(ValueError, match="准入范围"):
        validate_source_url("sse_announcement", "https://example.com/a.html")
    with pytest.raises(ValueError, match="合成评测"):
        validate_source_url("licensed_market_snapshot", "https://example.invalid/market.json")
