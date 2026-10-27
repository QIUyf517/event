"""独立存储与来源绑定的假设生成及反方核验记录。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.event_research import EventDebateRun


SCHEMA = """
CREATE TABLE IF NOT EXISTS debate_runs (
  run_id TEXT PRIMARY KEY,
  graph_run_id TEXT NOT NULL,
  subject_code TEXT NOT NULL,
  as_of_date TEXT NOT NULL,
  answer_status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debate_scope
ON debate_runs(subject_code, as_of_date, created_at);
"""


class EventDebateStore:
    def __init__(self, path: str | Path, *, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self.conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.executescript(SCHEMA)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def save(self, run: EventDebateRun) -> None:
        if self.readonly:
            raise RuntimeError("只读研究报告库不能保存结果")
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO debate_runs VALUES (?,?,?,?,?,?,?)",
                (run.run_id, run.graph_run_id, run.graph.subject_code,
                 run.graph.as_of_date.isoformat(), run.conclusion.answer_status,
                 run.created_at.isoformat(), run.model_dump_json()),
            )

    def get(self, run_id: str) -> EventDebateRun | None:
        row = self.conn.execute("SELECT payload FROM debate_runs WHERE run_id=?", (run_id,)).fetchone()
        return EventDebateRun.model_validate_json(row["payload"]) if row else None

    def latest(self, subject_code: str, as_of_date: str | None = None) -> EventDebateRun | None:
        if as_of_date is None:
            row = self.conn.execute(
                "SELECT payload FROM debate_runs WHERE subject_code=? "
                "ORDER BY created_at DESC, run_id DESC LIMIT 1", (subject_code,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT payload FROM debate_runs WHERE subject_code=? AND as_of_date=? "
                "ORDER BY created_at DESC, run_id DESC LIMIT 1",
                (subject_code, as_of_date)).fetchone()
        return EventDebateRun.model_validate_json(row["payload"]) if row else None
