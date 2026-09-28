import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from app.models.research import Document, DocumentChunk, Evidence, ETFProfile, MarketBar, ShareRecord, SignalResult, SyncResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_master (etf_code TEXT PRIMARY KEY, name TEXT NOT NULL, fund_company TEXT, index_name TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS etf_daily (etf_code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, volume REAL, turnover REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(etf_code, trade_date));
CREATE TABLE IF NOT EXISTS etf_shares (etf_code TEXT NOT NULL, trade_date TEXT NOT NULL, shares REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(etf_code, trade_date));
CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, source_url TEXT NOT NULL, publisher TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, document_type TEXT NOT NULL, etf_codes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, content TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL, published_at TEXT NOT NULL, page INTEGER, location TEXT);
CREATE TABLE IF NOT EXISTS signal_results (etf_code TEXT NOT NULL, as_of_date TEXT NOT NULL, payload TEXT NOT NULL, algorithm_version TEXT NOT NULL, PRIMARY KEY(etf_code, as_of_date, algorithm_version));
CREATE TABLE IF NOT EXISTS research_tasks (task_id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (evidence_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_reports (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_runs (sync_id TEXT PRIMARY KEY, etf_code TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_calls (call_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, status TEXT NOT NULL, input_payload TEXT NOT NULL, output_payload TEXT NOT NULL, created_at TEXT NOT NULL);
"""

class SQLiteRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_column("document_chunks", "location", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self):
        self.conn.close()

    def upsert_profile(self, profile: ETFProfile):
        self.conn.execute("INSERT OR REPLACE INTO etf_master(etf_code,name,fund_company,index_name,source) VALUES (?, ?, ?, ?, ?)", (profile.etf_code, profile.name, profile.fund_company, profile.index_name, profile.source))
        self.conn.commit()

    def upsert_market(self, bars: Iterable[MarketBar]):
        self.conn.executemany("INSERT OR REPLACE INTO etf_daily(etf_code,trade_date,close,volume,turnover,source,fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [(b.etf_code, b.trade_date.isoformat(), b.close, b.volume, b.turnover, b.source, b.fetched_at.isoformat()) for b in bars])
        self.conn.commit()

    def upsert_shares(self, rows: Iterable[ShareRecord]):
        self.conn.executemany("INSERT OR REPLACE INTO etf_shares(etf_code,trade_date,shares,source,fetched_at) VALUES (?, ?, ?, ?, ?)", [(r.etf_code, r.trade_date.isoformat(), r.shares, r.source, r.fetched_at.isoformat()) for r in rows])
        self.conn.commit()

    def upsert_document(self, doc: Document, chunks: Iterable[DocumentChunk]):
        self.conn.execute("INSERT OR REPLACE INTO documents(document_id,title,content,source_url,publisher,published_at,fetched_at,document_type,etf_codes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (doc.document_id, doc.title, doc.content, doc.source_url, doc.publisher, doc.published_at.isoformat(), doc.fetched_at.isoformat(), doc.document_type, json.dumps(doc.etf_codes, ensure_ascii=False)))
        self.conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc.document_id,))
        self.conn.executemany("INSERT OR REPLACE INTO document_chunks(chunk_id,document_id,content,title,source_url,published_at,page,location) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(c.chunk_id, c.document_id, c.content, c.title, c.source_url, c.published_at.isoformat(), c.page, c.location) for c in chunks])
        self.conn.commit()

    def market(self, etf_code: str, end_date: date, limit: int = 365) -> list[MarketBar]:
        rows = self.conn.execute("SELECT * FROM etf_daily WHERE etf_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?", (etf_code, end_date.isoformat(), limit)).fetchall()
        return [MarketBar(etf_code=r["etf_code"], trade_date=date.fromisoformat(r["trade_date"]), close=r["close"], volume=r["volume"], turnover=r["turnover"], source=r["source"], fetched_at=datetime.fromisoformat(r["fetched_at"])) for r in reversed(rows)]

    def shares(self, etf_code: str, end_date: date, limit: int = 365) -> list[ShareRecord]:
        rows = self.conn.execute("SELECT * FROM etf_shares WHERE etf_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?", (etf_code, end_date.isoformat(), limit)).fetchall()
        return [ShareRecord(etf_code=r["etf_code"], trade_date=date.fromisoformat(r["trade_date"]), shares=r["shares"], source=r["source"], fetched_at=datetime.fromisoformat(r["fetched_at"])) for r in reversed(rows)]

    def profile(self, etf_code: str) -> ETFProfile | None:
        r = self.conn.execute("SELECT * FROM etf_master WHERE etf_code=?", (etf_code,)).fetchone()
        return ETFProfile(**dict(r)) if r else None

    def search_chunks(self, query: str, etf_code: str | None = None, date_range: tuple[date, date] | None = None, document_types: list[str] | None = None, limit: int = 10) -> list[DocumentChunk]:
        terms = []
        for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query.lower()):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 4:
                terms.extend(token[i:i + size] for size in (4, 3, 2) for i in range(len(token) - size + 1))
            else:
                terms.append(token)
        clauses, params = [], []
        if etf_code:
            clauses.append("d.etf_codes LIKE ?")
            params.append(f"%{etf_code}%")
        if date_range:
            clauses.append("c.published_at BETWEEN ? AND ?")
            params.extend([date_range[0].isoformat(), date_range[1].isoformat()])
        if document_types:
            clauses.append(f"d.document_type IN ({','.join('?' for _ in document_types)})")
            params.extend(document_types)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT c.* FROM document_chunks c JOIN documents d ON d.document_id=c.document_id {where} ORDER BY c.published_at DESC", params).fetchall()
        ranked = []
        for r in rows:
            haystack = f"{r['title']} {r['content']}".lower()
            score = sum(haystack.count(t) for t in terms) if terms else 0
            if score:
                ranked.append((score, r))
        ranked.sort(key=lambda x: (x[0], x[1]["published_at"]), reverse=True)
        return [DocumentChunk(chunk_id=r["chunk_id"], document_id=r["document_id"], content=r["content"], title=r["title"], source_url=r["source_url"], published_at=date.fromisoformat(r["published_at"]), page=r["page"], location=r["location"]) for _, r in ranked[:limit]]

    def save_signal(self, result: SignalResult):
        self.conn.execute("INSERT OR REPLACE INTO signal_results VALUES (?, ?, ?, ?)", (result.etf_code, result.as_of_date.isoformat(), result.model_dump_json(), result.algorithm_version))
        self.conn.commit()

    def save_task(self, task_id: str, question: str, status: str = "running"):
        self.conn.execute("INSERT OR REPLACE INTO research_tasks VALUES (?, ?, ?, ?)", (task_id, question, status, datetime.utcnow().isoformat()))
        self.conn.commit()

    def update_task_status(self, task_id: str, status: str):
        self.conn.execute("UPDATE research_tasks SET status=? WHERE task_id=?", (status, task_id))
        self.conn.commit()

    def save_evidence(self, task_id: str, items: Iterable[Evidence]):
        self.conn.executemany("INSERT OR REPLACE INTO evidence(evidence_id,task_id,payload) VALUES (?, ?, ?)", [(item.evidence_id, task_id, item.model_dump_json()) for item in items])
        self.conn.commit()

    def save_tool_call(self, call_id: str, task_id: str, tool_name: str, status: str, input_payload: dict, output_payload):
        self.conn.execute("INSERT OR REPLACE INTO tool_calls VALUES (?, ?, ?, ?, ?, ?, ?)", (call_id, task_id, tool_name, status, json.dumps(input_payload, ensure_ascii=False, default=str), json.dumps(output_payload, ensure_ascii=False, default=str), datetime.utcnow().isoformat()))
        self.conn.commit()

    def save_sync_result(self, result: SyncResult):
        self.conn.execute("INSERT OR REPLACE INTO sync_runs VALUES (?, ?, ?, ?, ?)", (result.sync_id, result.etf_code, result.market_status, result.model_dump_json(), datetime.utcnow().isoformat()))
        self.conn.commit()

    def get_document_chunk(self, chunk_id: str) -> DocumentChunk | None:
        r = self.conn.execute("SELECT * FROM document_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        if not r:
            return None
        return DocumentChunk(chunk_id=r["chunk_id"], document_id=r["document_id"], content=r["content"], title=r["title"], source_url=r["source_url"], published_at=date.fromisoformat(r["published_at"]), page=r["page"], location=r["location"])

    def save_report(self, task_id: str, report: dict):
        self.conn.execute("INSERT OR REPLACE INTO research_reports VALUES (?, ?, ?)", (task_id, json.dumps(report, ensure_ascii=False, default=str), datetime.utcnow().isoformat()))
        self.conn.commit()

