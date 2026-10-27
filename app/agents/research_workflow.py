"""User-triggered local workflow with durable stage snapshots.

Jobs run in-process, not in a scheduler. A process restart marks unfinished jobs
interrupted; reading a saved run never resumes paid model calls.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.agents.event_orchestrator import EventResearchOrchestrator
from app.agents.event_graph_builder import EventGraphBuilder
from app.agents.typed_hypothesis import TypedEventHypothesisDebate as EventHypothesisDebate
from app.reporting.event_report import public_debate


class WorkflowStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, run: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS research_runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO research_runs VALUES (?, ?)",
                         (run["run_id"], json.dumps(run, ensure_ascii=False)))

    def get(self, run_id: str) -> dict | None:
        if not self.path.is_file():
            return None
        with sqlite3.connect(self.path, timeout=10) as conn:
            row = conn.execute("SELECT payload FROM research_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def interrupt_unfinished(self):
        if not self.path.is_file():
            return
        with sqlite3.connect(self.path, timeout=10) as conn:
            for run_id, payload in conn.execute("SELECT id,payload FROM research_runs").fetchall():
                run = json.loads(payload)
                if run["status"] in {"queued", "running"}:
                    run.update(status="interrupted", stop_reason="服务重启中断；已保存结果仍可读取，不会自动续费运行。")
                    for stage in run["stages"]:
                        if stage["status"] == "running":
                            stage["status"] = "interrupted"
                    conn.execute("UPDATE research_runs SET payload=? WHERE id=?", (json.dumps(run, ensure_ascii=False), run_id))


def new_run(request: dict) -> dict:
    return {"run_id": f"research-{uuid4().hex}", **request, "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": 0,
            "total_tokens": 0, "estimated_cost_usd": 0, "stop_reason": "等待执行",
            "planning": None, "graph": None, "debate": None,
            "stages": [{"key": key, "label": label, "status": "pending", "elapsed_seconds": 0}
                       for key, label in [("planning", "计划与并行取证"), ("graph", "时间线与原文"),
                                          ("debate", "候选解释与反方核验"), ("report", "程序报告")]]}


def execute_run(run: dict, store: WorkflowStore, source: Path, graph_path: Path, debate_path: Path):
    started = monotonic()
    run["status"] = "running"
    try:
        for stage in run["stages"]:
            stage.update(status="running")
            stage_start = monotonic()
            store.save(run)
            if stage["key"] == "planning":
                planning = EventResearchOrchestrator(source).run(run["question"], run["subject_code"],
                    date.fromisoformat(run["as_of_date"]), date.fromisoformat(run["window_start"]))
                run["planning"] = planning.model_dump(mode="json")
                run.update(total_tokens=planning.total_tokens, estimated_cost_usd=planning.estimated_cost_usd)
            elif stage["key"] == "graph":
                graph = EventGraphBuilder(source, graph_path).build(planning)
                run["graph"] = graph.model_dump(mode="json")
                for item in run["graph"]["sources"]:
                    item.pop("snapshot_path", None)
                run.update(total_tokens=graph.planning_tokens + graph.extraction_tokens,
                           estimated_cost_usd=graph.estimated_cost_usd)
            elif stage["key"] == "debate":
                debate = EventHypothesisDebate(source, graph_path, debate_path).build(graph.run_id)
                run["debate"] = public_debate(debate)
                run["total_tokens"] += debate.model_tokens
                run["estimated_cost_usd"] += debate.estimated_cost_usd
                run["stop_reason"] = debate.stop_reason
            stage.update(status="completed", elapsed_seconds=round(monotonic() - stage_start, 3))
            run["elapsed_seconds"] = round(monotonic() - started, 3)
            store.save(run)
        run["status"] = "partial"  # The saved interpretation is not independently validated.
    except Exception as exc:
        stage.update(status="failed", elapsed_seconds=round(monotonic() - stage_start, 3))
        run.update(status="failed", stop_reason=f"{type(exc).__name__}：研究中止，已保留完成阶段。")
    finally:
        run["elapsed_seconds"] = round(monotonic() - started, 3)
        store.save(run)
