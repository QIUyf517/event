"""Research UI backend boundaries and evaluation integrity."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.research_workflow import WorkflowStore, execute_run, new_run
from app.api.main import create_app
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.evaluation.event_research import load_event_cases
from app.evaluation.phase6 import packet_for, score
from app.models.event_research import EventDebateRun
from app.reporting.event_report import public_debate


REQUEST = dict(question="核对公开资料", subject_code="600519", window_start="2026-08-14", as_of_date="2026-09-13")


def test_saved_public_projection_never_republishes_legacy_model_prose():
    manifest = json.loads(Path("data/event_demo.json").read_text(encoding="utf-8"))
    for item in manifest["runs"]:
        run = EventDebateRun.model_validate(item["debate"])
        run.hypotheses[0].statement = "公司半年报文档日期是8月28日，来源各自独立。"
        run.conclusion.text = "公司已经确认了所有原因。"
        run.report_draft = "不应公开的模型草稿"
        public = public_debate(run)
        assert public["conclusion"]["answer_status"] == "partial"
        assert public["conclusion"]["selected_hypothesis_id"] is None
        assert "report_draft" not in public
        assert all("snapshot_path" not in source for source in public["graph"]["sources"])
        assert "8月28日" not in public["hypotheses"][0]["statement"]
        assert public["graph"]["citations"] == item["graph"]["citations"]


def test_failed_stage_preserves_planning_and_restart_does_not_resume(tmp_path, monkeypatch):
    import app.agents.research_workflow as module

    class Planning:
        total_tokens = 10
        estimated_cost_usd = .001
        def model_dump(self, **_):
            return {"plan": {"steps": []}, "evidence": [{"excerpt": "保留证据"}]}

    class Orchestrator:
        def __init__(self, *_): pass
        def run(self, *_): return Planning()

    class Builder:
        def __init__(self, *_): pass
        def build(self, *_): raise RuntimeError("source extraction failed")

    monkeypatch.setattr(module, "EventResearchOrchestrator", Orchestrator)
    monkeypatch.setattr(module, "EventGraphBuilder", Builder)
    store = WorkflowStore(tmp_path / "runs.sqlite3")
    run = new_run(REQUEST)
    execute_run(run, store, tmp_path / "source", tmp_path / "graph", tmp_path / "debate")
    saved = store.get(run["run_id"])
    assert saved["status"] == "failed"
    assert saved["planning"]["evidence"]
    assert saved["stages"][1]["status"] == "failed"
    assert saved["total_tokens"] == 10
    pending = new_run(REQUEST)
    pending["status"] = "running"
    pending["stages"][0]["status"] = "running"
    store.save(pending)
    store.interrupt_unfinished()
    assert store.get(pending["run_id"])["status"] == "interrupted"
    assert store.get(run["run_id"])["status"] == "failed"


def test_workflow_api_auth_and_scope_before_execution(tmp_path, monkeypatch):
    import app.api.main as module
    source = tmp_path / "source.sqlite3"
    repo = SQLiteRepository(source)
    seed_event_research_subjects(repo)
    repo.close()
    calls = []
    def fake_execute(run, store, *_):
        calls.append(run["run_id"])
        run.update(status="partial", stop_reason="test complete")
        store.save(run)
    monkeypatch.setattr(module, "execute_run", fake_execute)
    app = create_app(write_key="test-key", event_database_path=source,
                     event_graph_path=tmp_path / "graphs.sqlite3", event_debate_path=tmp_path / "debates.sqlite3")
    with TestClient(app) as client:
        url = "/api/v1/event-research/runs"
        assert client.post(url, json=REQUEST).status_code == 401
        headers = {"X-API-Key": "test-key"}
        for bad in [dict(subject_code="000001"), dict(window_start="2025-01-01"), dict(question="   ")]:
            assert client.post(url, json={**REQUEST, **bad}, headers=headers).status_code == 422
        assert not calls
        result = client.post(url, json=REQUEST, headers=headers)
        assert result.status_code == 202
        run_id = result.json()["run"]["run_id"]
        assert client.get(f"{url}/{run_id}").json()["run"]["status"] == "partial"
        assert len(calls) == 1
        client.get(f"{url}/{run_id}")
        assert len(calls) == 1  # Refresh is read-only.
        assert client.get(f"{url}/missing").status_code == 404
        monkeypatch.setenv("EVENT_DEMO_MODE", "true")
        assert client.post(url, json=REQUEST, headers=headers).status_code == 403


def test_eval_inputs_exclude_gold_and_future_records():
    for case in load_event_cases():
        packet = packet_for(case, ["shared-vocabulary"])
        assert "gold_answer" not in packet and "expected_events" not in packet
        assert "forbidden_conclusions" not in packet and "required_term_groups" not in packet
        assert all(record["published_at"] <= str(case.as_of_date) for record in packet["records"])
        bad = {"assertions": [{"text": "fake", "citations": [{"record_id": "not-saved", "quote": "fake"}]}]}
        metrics = score(case, bad, packet)
        assert metrics["unsupported_assertion_count"] == 1
        assert metrics["citation_exact_match_rate"] == 0
        assert metrics["invalid_or_future_reference_count"] == 1
