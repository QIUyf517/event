"""第四阶段事件图谱的独立 SQLite 存储，支持事务。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models.event_graph import EventGraphRun


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS graph_runs (
  run_id TEXT PRIMARY KEY, subject_code TEXT NOT NULL, window_start TEXT NOT NULL,
  as_of_date TEXT NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL,
  model TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_runs_scope ON graph_runs(subject_code, as_of_date, created_at);
CREATE TABLE IF NOT EXISTS graph_sources (
  run_id TEXT NOT NULL, document_id TEXT NOT NULL, source_id TEXT NOT NULL,
  source_type TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT NOT NULL,
  source_url TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL, snapshot_path TEXT, reprint_of TEXT,
  PRIMARY KEY(run_id, document_id),
  FOREIGN KEY(run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS graph_events (
  run_id TEXT NOT NULL, event_id TEXT NOT NULL, event_key TEXT NOT NULL,
  title TEXT NOT NULL, occurred_at TEXT, date_status TEXT NOT NULL,
  reported_at TEXT NOT NULL, importance TEXT NOT NULL,
  PRIMARY KEY(run_id, event_id),
  FOREIGN KEY(run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS graph_citations (
  run_id TEXT NOT NULL, citation_id TEXT NOT NULL, document_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL, quote TEXT NOT NULL, start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL, page INTEGER, location TEXT,
  PRIMARY KEY(run_id, citation_id),
  FOREIGN KEY(run_id, document_id) REFERENCES graph_sources(run_id, document_id)
);
CREATE TABLE IF NOT EXISTS graph_claims (
  run_id TEXT NOT NULL, claim_id TEXT NOT NULL, event_id TEXT NOT NULL,
  citation_id TEXT NOT NULL, attribute TEXT NOT NULL, value_text TEXT,
  qualifier TEXT, unit_context TEXT, numeric_status TEXT NOT NULL, calculation_note TEXT, text TEXT NOT NULL,
  PRIMARY KEY(run_id, claim_id),
  FOREIGN KEY(run_id, event_id) REFERENCES graph_events(run_id, event_id),
  FOREIGN KEY(run_id, citation_id) REFERENCES graph_citations(run_id, citation_id)
);
CREATE TABLE IF NOT EXISTS graph_conflicts (
  run_id TEXT NOT NULL, conflict_id TEXT NOT NULL, event_id TEXT NOT NULL,
  kind TEXT NOT NULL, attribute TEXT NOT NULL, variants_json TEXT NOT NULL,
  claim_ids_json TEXT NOT NULL,
  PRIMARY KEY(run_id, conflict_id),
  FOREIGN KEY(run_id, event_id) REFERENCES graph_events(run_id, event_id)
);
CREATE TABLE IF NOT EXISTS graph_relations (
  run_id TEXT NOT NULL, relation_id TEXT NOT NULL, event_id TEXT NOT NULL,
  left_claim_id TEXT NOT NULL, right_claim_id TEXT NOT NULL, kind TEXT NOT NULL,
  PRIMARY KEY(run_id, relation_id),
  FOREIGN KEY(run_id, event_id) REFERENCES graph_events(run_id, event_id),
  FOREIGN KEY(run_id, left_claim_id) REFERENCES graph_claims(run_id, claim_id),
  FOREIGN KEY(run_id, right_claim_id) REFERENCES graph_claims(run_id, claim_id)
);
"""


class EventGraphStore:
    def __init__(self, path: str | Path, *, readonly: bool = False):
        self.path = Path(path)
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self.conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.executescript(SCHEMA)
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(graph_claims)")}
            if "unit_context" not in columns:
                self.conn.execute("ALTER TABLE graph_claims ADD COLUMN unit_context TEXT")
            if "qualifier" not in columns:
                self.conn.execute("ALTER TABLE graph_claims ADD COLUMN qualifier TEXT")
            if "calculation_note" not in columns:
                self.conn.execute("ALTER TABLE graph_claims ADD COLUMN calculation_note TEXT")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.readonly = readonly

    def close(self):
        self.conn.close()

    def save(self, run: EventGraphRun) -> None:
        if self.readonly:
            raise RuntimeError("只读图谱库不能保存结果")
        with self.conn:
            for table in ("graph_relations", "graph_conflicts", "graph_claims", "graph_citations",
                          "graph_events", "graph_sources"):
                self.conn.execute(f"DELETE FROM {table} WHERE run_id=?", (run.run_id,))
            self.conn.execute("DELETE FROM graph_runs WHERE run_id=?", (run.run_id,))
            self.conn.execute(
                "INSERT INTO graph_runs VALUES (?,?,?,?,?,?,?,?,?)",
                (run.run_id, run.subject_code, run.window_start.isoformat(),
                 run.as_of_date.isoformat(), run.question, run.status, run.model,
                 run.created_at.isoformat(), run.model_dump_json()))
            self.conn.executemany("INSERT INTO graph_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                (run.run_id, item.document_id, item.source_id, item.source_type,
                 item.title, item.publisher, item.source_url, item.published_at.isoformat(),
                 item.fetched_at.isoformat(), item.content_hash, item.snapshot_path,
                 item.reprint_of) for item in run.sources])
            self.conn.executemany("INSERT INTO graph_events VALUES (?,?,?,?,?,?,?,?)", [
                (run.run_id, item.event_id, item.event_key, item.title,
                 item.occurred_at.isoformat() if item.occurred_at else None,
                 item.date_status, item.reported_at.isoformat(), item.importance)
                for item in run.events])
            self.conn.executemany("INSERT INTO graph_citations VALUES (?,?,?,?,?,?,?,?,?)", [
                (run.run_id, item.citation_id, item.document_id, item.chunk_id,
                 item.quote, item.start_offset, item.end_offset, item.page, item.location)
                for item in run.citations])
            self.conn.executemany("INSERT INTO graph_claims "
                "(run_id,claim_id,event_id,citation_id,attribute,value_text,qualifier,unit_context,numeric_status,calculation_note,text) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                (run.run_id, item.claim_id, item.event_id, item.citation_id,
                 item.attribute, item.value_text, item.qualifier, item.unit_context,
                 item.numeric_status, item.calculation_note, item.text)
                for item in run.claims])
            self.conn.executemany("INSERT INTO graph_conflicts VALUES (?,?,?,?,?,?,?)", [
                (run.run_id, item.conflict_id, item.event_id, item.kind,
                 item.attribute, json.dumps(item.variants, ensure_ascii=False),
                 json.dumps(item.claim_ids, ensure_ascii=False))
                for item in run.conflicts])
            self.conn.executemany("INSERT INTO graph_relations VALUES (?,?,?,?,?,?)", [
                (run.run_id, item.relation_id, item.event_id, item.left_claim_id,
                 item.right_claim_id, item.kind) for item in run.relations])

    def get(self, run_id: str) -> EventGraphRun | None:
        row = self.conn.execute("SELECT payload FROM graph_runs WHERE run_id=?", (run_id,)).fetchone()
        return EventGraphRun.model_validate_json(row["payload"]) if row else None

    def latest(self, subject_code: str, as_of_date: str | None = None) -> EventGraphRun | None:
        query = "SELECT payload FROM graph_runs WHERE subject_code=?"
        params: list[str] = [subject_code]
        if as_of_date:
            query += " AND as_of_date=?"
            params.append(as_of_date)
        query += " ORDER BY created_at DESC, run_id DESC LIMIT 1"
        row = self.conn.execute(query, params).fetchone()
        return EventGraphRun.model_validate_json(row["payload"]) if row else None
