from datetime import date, datetime

from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import Document, DocumentChunk


def test_retrieval_prefers_matching_body_and_limits_one_long_document(tmp_path):
    repo = SQLiteRepository(tmp_path / "rank.sqlite3")
    published = date(2026, 2, 9)
    title = "510050 IOPV 计算机构公告"
    doc = Document(document_id="long", title=title, content="测试", source_url="https://www.sse.com.cn/test.pdf",
                   publisher="上海证券交易所", published_at=published, fetched_at=datetime.now(),
                   document_type="fund_announcement", etf_codes=["510050"])
    chunks = [DocumentChunk(chunk_id=f"c{i}", document_id="long", title=title,
        content=("510050 IOPV计算机构由中证指数有限公司承担" if i == 3 else f"无关章节{i}"),
        source_url=doc.source_url, published_at=published, page=i) for i in range(1, 6)]
    repo.upsert_document(doc, chunks)
    found = repo.search_chunks("510050 IOPV 计算机构", "510050", limit=10)
    assert found[0].chunk_id == "c3"
    assert len(found) == 2
    repo.close()
