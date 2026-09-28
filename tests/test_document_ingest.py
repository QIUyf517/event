from datetime import date

import pytest
from app.data.repositories.sqlite import SQLiteRepository
from app.rag.ingest import build_document, ingest_official_url

def test_html_ingest_preserves_section_and_paragraph_location():
    payload = "<html><h2>基金份额</h2><p>510300份额信息。</p><p>数据以公告为准。</p></html>".encode()
    document, chunks = build_document(payload, "report.html", "定期报告", "上海证券交易所", date(2026, 1, 1), "https://www.sse.com.cn/report.html", "fund_report")
    assert document.source_url.startswith("https://www.sse.com.cn")
    assert chunks[0].location == "section:基金份额;paragraph:1"

def test_pdf_ingest_preserves_page_number(monkeypatch):
    class Page:
        def extract_text(self): return "第一页的基金报告正文"
    class Reader:
        def __init__(self, _): self.pages = [Page()]
    monkeypatch.setattr("app.rag.ingest.PdfReader", Reader)
    _, chunks = build_document(b"pdf", "report.pdf", "报告", "基金公司", date(2026, 1, 1), "https://www.sse.com.cn/report.pdf", "fund_report")
    assert chunks[0].page == 1
    assert chunks[0].location == "page:1"

def test_official_collector_rejects_unknown_domain(tmp_path):
    repo = SQLiteRepository(tmp_path / "docs.sqlite3")
    with pytest.raises(ValueError, match="非允许"):
        ingest_official_url(repo, "https://example.com/report.pdf", title="x", publisher="x", published_at=date(2026, 1, 1), document_type="fund_report")
    repo.close()
