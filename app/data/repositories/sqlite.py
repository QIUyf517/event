"""使用 SQLite 存储企业文档与历史行情上下文。"""

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from app.models.research import (Document, DocumentChunk, DocumentStoreResult,
                                 ResearchRetrievalResult, ResearchSubject)

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_market_daily (subject_id TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL, volume REAL, turnover REAL, source TEXT NOT NULL, source_url TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(subject_id, trade_date));
CREATE TABLE IF NOT EXISTS research_subjects (subject_id TEXT PRIMARY KEY, subject_type TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, aliases TEXT NOT NULL, market TEXT, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, source_url TEXT NOT NULL, publisher TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, document_type TEXT NOT NULL, subject_ids TEXT NOT NULL DEFAULT '[]', source_id TEXT NOT NULL DEFAULT '', canonical_url TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '', snapshot_path TEXT, duplicate_of TEXT, reprint_of TEXT);
CREATE TABLE IF NOT EXISTS document_chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, content TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL, published_at TEXT NOT NULL, page INTEGER, location TEXT, subject_ids TEXT NOT NULL DEFAULT '[]', source_id TEXT NOT NULL DEFAULT '', document_type TEXT NOT NULL DEFAULT '', publisher TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '');
"""


class SQLiteRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        if "etf_codes" in {row[1] for row in self.conn.execute("PRAGMA table_info(documents)")}:
            self.conn.execute("ALTER TABLE documents DROP COLUMN etf_codes")
        self._ensure_column("document_chunks", "location", "TEXT")
        for column, definition in (
            ("subject_ids", "TEXT NOT NULL DEFAULT '[]'"), ("source_id", "TEXT NOT NULL DEFAULT ''"),
            ("canonical_url", "TEXT NOT NULL DEFAULT ''"), ("content_hash", "TEXT NOT NULL DEFAULT ''"),
            ("snapshot_path", "TEXT"), ("duplicate_of", "TEXT"), ("reprint_of", "TEXT")):
            self._ensure_column("documents", column, definition)
        for column, definition in (
            ("subject_ids", "TEXT NOT NULL DEFAULT '[]'"), ("source_id", "TEXT NOT NULL DEFAULT ''"),
            ("document_type", "TEXT NOT NULL DEFAULT ''"), ("publisher", "TEXT NOT NULL DEFAULT ''"),
            ("content_hash", "TEXT NOT NULL DEFAULT ''")):
            self._ensure_column("document_chunks", column, definition)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_canonical_url ON documents(canonical_url)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents(published_at)")
        self.conn.commit()

    @classmethod
    def open_readonly(cls, path: str | Path):
        """给并行研究任务各开一个独立的只读连接。"""
        instance = cls.__new__(cls)
        instance.path = Path(path)
        instance.conn = sqlite3.connect(instance.path.resolve().as_uri() + "?mode=ro", uri=True)
        instance.conn.row_factory = sqlite3.Row
        return instance

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self):
        self.conn.close()

    def upsert_research_subject(self, subject: ResearchSubject):
        self.conn.execute(
            "INSERT OR REPLACE INTO research_subjects(subject_id,subject_type,code,name,aliases,market,enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (subject.subject_id, subject.subject_type, subject.code, subject.name,
             json.dumps(subject.aliases, ensure_ascii=False), subject.market, int(subject.enabled)))
        self.conn.commit()

    def research_subject(self, subject_id: str) -> ResearchSubject | None:
        row = self.conn.execute("SELECT * FROM research_subjects WHERE subject_id=?", (subject_id,)).fetchone()
        if not row:
            return None
        return ResearchSubject(subject_id=row["subject_id"], subject_type=row["subject_type"],
            code=row["code"], name=row["name"], aliases=json.loads(row["aliases"]),
            market=row["market"], enabled=bool(row["enabled"]))

    def list_research_subjects(self) -> list[ResearchSubject]:
        rows = self.conn.execute("SELECT subject_id FROM research_subjects WHERE enabled=1 ORDER BY subject_id").fetchall()
        return [self.research_subject(row["subject_id"]) for row in rows]

    def upsert_company_market(self, rows: Iterable[dict]):
        self.conn.executemany(
            "INSERT OR REPLACE INTO company_market_daily(subject_id,trade_date,close,volume,turnover,source,source_url,fetched_at) VALUES (?,?,?,?,?,?,?,?)",
            [(r["subject_id"], r["trade_date"], r["close"], r.get("volume"),
              r.get("turnover"), r["source"], r["source_url"], r["fetched_at"]) for r in rows])
        self.conn.commit()

    def company_market(self, subject_id: str, start_date: date, end_date: date, limit: int = 31) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM company_market_daily WHERE subject_id=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date DESC LIMIT ?",
            (subject_id, start_date.isoformat(), end_date.isoformat(), limit)).fetchall()
        return [dict(row) for row in rows]

    def upsert_document(self, doc: Document, chunks: Iterable[DocumentChunk]):
        self.upsert_documents([(doc, chunks)])

    def upsert_documents(self, items: Iterable[tuple[Document, Iterable[DocumentChunk]]]):
        """以原子操作导入已预先校验的一批资料。"""
        try:
            with self.conn:
                for doc, chunks in items:
                    self.conn.execute("INSERT OR REPLACE INTO documents(document_id,title,content,source_url,publisher,published_at,fetched_at,document_type,subject_ids,source_id,canonical_url,content_hash,snapshot_path,duplicate_of,reprint_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (doc.document_id, doc.title, doc.content, doc.source_url, doc.publisher, doc.published_at.isoformat(), doc.fetched_at.isoformat(), doc.document_type, json.dumps(doc.subject_ids, ensure_ascii=False), doc.source_id, doc.canonical_url, doc.content_hash, doc.snapshot_path, doc.duplicate_of, doc.reprint_of))
                    self.conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc.document_id,))
                    self.conn.executemany("INSERT OR REPLACE INTO document_chunks(chunk_id,document_id,content,title,source_url,published_at,page,location,subject_ids,source_id,document_type,publisher,content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(c.chunk_id, c.document_id, c.content, c.title, c.source_url, c.published_at.isoformat(), c.page, c.location, json.dumps(c.subject_ids, ensure_ascii=False), c.source_id, c.document_type, c.publisher, c.content_hash) for c in chunks])
        except Exception:
            self.conn.rollback()
            raise

    def store_event_document(self, doc: Document, chunks: Iterable[DocumentChunk]) -> DocumentStoreResult:
        """保存规范化来源；网址、内容或标题完全重复的资料不构成独立证据。"""
        candidates = []
        if doc.reprint_of:
            row = self.conn.execute("SELECT document_id FROM documents WHERE document_id=?", (doc.reprint_of,)).fetchone()
            if row:
                candidates.append((row["document_id"], "reprint_of"))
        if doc.canonical_url:
            row = self.conn.execute("SELECT document_id FROM documents WHERE canonical_url=?", (doc.canonical_url,)).fetchone()
            if row:
                candidates.append((row["document_id"], "canonical_url"))
        if doc.content_hash:
            row = self.conn.execute("SELECT document_id FROM documents WHERE content_hash=?", (doc.content_hash,)).fetchone()
            if row:
                candidates.append((row["document_id"], "content_hash"))
        normalized_title = re.sub(r"\s+", "", doc.title).lower()
        for row in self.conn.execute("SELECT document_id,title,published_at,subject_ids FROM documents WHERE published_at=?", (doc.published_at.isoformat(),)).fetchall():
            if (re.sub(r"\s+", "", row["title"]).lower() == normalized_title
                    and set(json.loads(row["subject_ids"] or "[]")) & set(doc.subject_ids)):
                candidates.append((row["document_id"], "title"))
                break
        if candidates:
            canonical_id, reason = candidates[0]
            return DocumentStoreResult(status="duplicate", document_id=doc.document_id,
                canonical_document_id=canonical_id, duplicate_reason=reason)
        exists = self.conn.execute("SELECT 1 FROM documents WHERE document_id=?", (doc.document_id,)).fetchone()
        self.upsert_document(doc, chunks)
        return DocumentStoreResult(status="updated" if exists else "inserted", document_id=doc.document_id,
            canonical_document_id=doc.document_id, duplicate_reason="none")

    def search_event_chunks(self, query: str, subject_id: str, as_of_date: date,
                            window_start: date | None = None, source_types: list[str] | None = None,
                            limit: int = 10) -> list[ResearchRetrievalResult]:
        """确定性混合检索：结合词项与字符匹配，并严格按元数据过滤。"""
        if window_start and window_start > as_of_date:
            raise ValueError("研究窗口开始日期不能晚于截止日期")
        clauses = ["d.subject_ids LIKE ?", "d.published_at <= ?", "d.duplicate_of IS NULL"]
        params: list[object] = [f'%"{subject_id}"%', as_of_date.isoformat()]
        if window_start:
            clauses.append("d.published_at >= ?")
            params.append(window_start.isoformat())
        if source_types:
            clauses.append(f"d.document_type IN ({','.join('?' for _ in source_types)})")
            params.extend(source_types)
        rows = self.conn.execute(
            "SELECT c.*,d.subject_ids,d.source_id,d.document_type,d.publisher,d.content_hash "
            "FROM document_chunks c JOIN documents d ON d.document_id=c.document_id WHERE "
            + " AND ".join(clauses), params).fetchall()
        normalized_query = re.sub(r"\s+", "", query.lower())
        tokens = list(dict.fromkeys(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", query.lower())))
        ngrams = list(dict.fromkeys(normalized_query[i:i + 2] for i in range(max(0, len(normalized_query) - 1))))
        ranked: list[tuple[float, str, sqlite3.Row]] = []
        navigation_words = {
            "首页", "新闻", "快讯", "要闻", "金融", "评论", "产经", "创投", "滚动",
            "投资", "视频", "直播", "数据", "服务", "下载", "登录", "返回", "新股", "基金",
            "公司", "a股", "港美股", "路演", "看资金", "追风向", "解财报",
        }
        for row in rows:
            title, content = row["title"].lower(), row["content"].lower()
            if (len(content.strip()) <= 4 and not re.search(r"\d|[。；，,:：]", content)):
                continue
            content_words = content.split()
            if content_words and all(word in navigation_words for word in content_words):
                continue
            if (len(content.strip()) < 80 and not re.search(r"\d|[。；，,:：]", content)
                    and tokens and not any(term in content for term in tokens)):
                continue
            token_score = sum(title.count(term) * 4 + content.count(term) * 2 for term in tokens)
            ngram_score = sum((title.count(term) * 2 + content.count(term)) for term in ngrams)
            if query.strip() and not token_score and not ngram_score:
                continue
            score = float(token_score) + min(float(ngram_score) / max(len(ngrams), 1), 5.0)
            ranked.append((score, row["published_at"], row))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results, per_document = [], {}
        for score, _, row in ranked:
            if per_document.get(row["document_id"], 0) >= 2:
                continue
            chunk = DocumentChunk(chunk_id=row["chunk_id"], document_id=row["document_id"],
                content=row["content"], title=row["title"], source_url=row["source_url"],
                published_at=date.fromisoformat(row["published_at"]), page=row["page"],
                location=row["location"], subject_ids=json.loads(row["subject_ids"] or "[]"),
                source_id=row["source_id"], document_type=row["document_type"],
                publisher=row["publisher"], content_hash=row["content_hash"])
            results.append(ResearchRetrievalResult(chunk=chunk, score=score))
            per_document[row["document_id"]] = per_document.get(row["document_id"], 0) + 1
            if len(results) >= limit:
                break
        return results

    def event_document(self, document_id: str) -> Document | None:
        row = self.conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if not row:
            return None
        return Document(document_id=row["document_id"], title=row["title"], content=row["content"],
            source_url=row["source_url"], publisher=row["publisher"],
            published_at=date.fromisoformat(row["published_at"]), fetched_at=datetime.fromisoformat(row["fetched_at"]),
            document_type=row["document_type"],
            subject_ids=json.loads(row["subject_ids"] or "[]"), source_id=row["source_id"],
            canonical_url=row["canonical_url"], content_hash=row["content_hash"],
            snapshot_path=row["snapshot_path"], duplicate_of=row["duplicate_of"], reprint_of=row["reprint_of"])

    def event_material_counts(self, subject_id: str | None = None) -> dict[str, int]:
        where, params = "", []
        if subject_id:
            where, params = " WHERE subject_ids LIKE ?", [f'%"{subject_id}"%']
        documents = self.conn.execute("SELECT COUNT(*) FROM documents" + where, params).fetchone()[0]
        chunk_where = ""
        if subject_id:
            chunk_where = " WHERE d.subject_ids LIKE ?"
        chunks = self.conn.execute("SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.document_id=c.document_id" + chunk_where, params).fetchone()[0]
        metadata = self.conn.execute("SELECT COUNT(*) FROM document_chunks c JOIN documents d ON d.document_id=c.document_id" + chunk_where + (" AND" if chunk_where else " WHERE") + " c.document_id=d.document_id AND c.source_url=d.source_url AND c.published_at=d.published_at", params).fetchone()[0]
        return {"documents": documents, "chunks": chunks, "metadata_aligned_chunks": metadata}
