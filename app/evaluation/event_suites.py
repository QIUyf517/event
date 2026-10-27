"""将合成题回答基准与真实来源可用性检查分开处理。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.config import PROJECT_ROOT
from app.data.repositories.sqlite import SQLiteRepository
from app.evaluation.event_research import load_event_cases
from app.tools.research_tools import EVENT_SUBJECTS


REAL_CASES_PATH = PROJECT_ROOT / "evals" / "event_research_real_snapshot_cases.json"


class ExpectedRealSource(BaseModel):
    source_id: str
    document_type: Literal["announcement", "news", "financial"]
    published_at: date


class RealSnapshotCase(BaseModel):
    id: str
    subject_code: str
    question: str
    window_start: date
    as_of_date: date
    expected_sources: list[ExpectedRealSource] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.subject_code not in EVENT_SUBJECTS:
            raise ValueError("真实样本题的研究对象不在允许范围内")
        if self.window_start > self.as_of_date or (self.as_of_date - self.window_start).days > 30:
            raise ValueError("真实样本题的日期窗口无效")
        if any(not self.window_start <= source.published_at <= self.as_of_date
               for source in self.expected_sources):
            raise ValueError("真实样本题的来源日期超出研究窗口")
        return self


def load_real_snapshot_cases(path: Path = REAL_CASES_PATH) -> list[RealSnapshotCase]:
    cases = [RealSnapshotCase.model_validate(item)
             for item in json.loads(path.read_text(encoding="utf-8"))]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("真实样本题ID必须唯一")
    return cases


def audit_event_suites(database_path: Path) -> dict:
    """仅检查语料是否就绪，不将合成题标准答案用于真实来源研究。"""
    synthetic = load_event_cases()
    real = load_real_snapshot_cases()
    result = {
        "synthetic_benchmark": {"dataset": "artificially_authored_cases",
                                "case_count": len(synthetic),
                                "status": "annotations_only_not_executed",
                                "answer_score": None},
        "real_snapshot": {"dataset": "saved_public_source_snapshots", "case_count": len(real),
                          "status": "database_missing", "cases": [],
                          "answer_score": None},
        "combined_answer_score": None,
        "limitations": [
            "人工合成题没有对应的真实快照，不在真实资料库上执行其标准答案评分。",
            "真实快照检查只证明指定来源可检索，不证明模型理解、引文忠实或因果判断正确。",
        ],
    }
    if not Path(database_path).is_file():
        return result
    repo = SQLiteRepository.open_readonly(database_path)
    try:
        checked = []
        for case in real:
            rows = repo.conn.execute(
                "SELECT d.source_id,d.document_type,d.published_at,COUNT(c.chunk_id) AS chunks "
                "FROM documents d LEFT JOIN document_chunks c ON c.document_id=d.document_id "
                "WHERE d.duplicate_of IS NULL AND d.subject_ids LIKE ? "
                "AND d.published_at BETWEEN ? AND ? "
                "GROUP BY d.document_id",
                (f'%"{case.subject_code}"%', case.window_start.isoformat(),
                 case.as_of_date.isoformat())).fetchall()
            stored = {(row["source_id"], row["document_type"], row["published_at"])
                      for row in rows if row["chunks"] > 0}
            hits = repo.search_event_chunks("", case.subject_code, case.as_of_date,
                window_start=case.window_start, limit=30)
            retrieved = {(hit.chunk.source_id, hit.chunk.document_type,
                          hit.chunk.published_at.isoformat()) for hit in hits}
            available = stored & retrieved
            expected = {(item.source_id, item.document_type, item.published_at.isoformat())
                        for item in case.expected_sources}
            missing = sorted(expected - available)
            checked.append({"case_id": case.id, "status": "retrieval_ready" if not missing else "missing_sources",
                            "expected_source_count": len(expected),
                            "matched_source_count": len(expected) - len(missing),
                            "missing_sources": [list(item) for item in missing]})
        result["real_snapshot"].update({
            "status": "retrieval_ready" if all(item["status"] == "retrieval_ready" for item in checked)
                      else "missing_sources", "cases": checked})
        return result
    finally:
        repo.close()
