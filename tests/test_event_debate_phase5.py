"""Phase-five behavior with isolated source, graph, and report stores."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.agents.hypothesis_debate import EventHypothesisDebate, _plain_text
from app.api.main import create_app
from app.data.repositories.event_debate import EventDebateStore
from app.data.repositories.event_graph import EventGraphStore
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.models.event_graph import (EventGraphRun, GraphCitation, GraphClaim, GraphEvent,
                                    GraphSource)
from app.rag.ingest import build_document


START, END = date(2026, 8, 14), date(2026, 9, 13)


def test_internal_aliases_are_rendered_as_readable_public_labels():
    assert _plain_text("h3优于h2，c1与c2不矛盾，另见c4。") == (
        "候选解释三优于候选解释二，所引两条原文不矛盾，另见所引原文。")
    assert _plain_text("资料库published_at；c1-c2-c3同源") == (
        "资料库发布日期；所引多条原文同源")
    assert _plain_text("event-2908308ce5a4643647e1 来自 688981-4cbc245b85b2da6a4169，见cP12") == (
        "对应事件 来自 所引公告，见所引原文")


class FakeDebateLLM:
    configured = True
    model = "fake-model"

    def __init__(self, *, invalid_claim=False, invalid_counter=False,
                 rounded_conflict=False, false_missing=False, self_negating=False,
                 all_rejected=False, empty_findings=False, malformed_fields=False):
        self.calls = 0
        self.invalid_claim = invalid_claim
        self.invalid_counter = invalid_counter
        self.rounded_conflict = rounded_conflict
        self.false_missing = false_missing
        self.self_negating = self_negating
        self.all_rejected = all_rejected
        self.empty_findings = empty_findings
        self.malformed_fields = malformed_fields

    def chat(self, messages, **kwargs):
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        if self.calls == 1:
            claims = [item["claim_id"] for item in payload["claims"]]
            if self.malformed_fields:
                answer = {"hypotheses": [{"statement": "字段不完整"}]}
                return {"message": {"content": json.dumps(answer, ensure_ascii=False)},
                        "usage": {"total_tokens": 100, "prompt_tokens": 60,
                                  "completion_tokens": 40}, "model": self.model}
            if self.invalid_claim:
                claims = ["foreign-run-claim"]
            if self.all_rejected:
                claims = ["c2"]
            answer = {"hypotheses": [{"statement": (
                "公司已于2026年8月18日正式披露" if self.all_rejected else
                "披露的业绩变化可能关联研究问题"),
                "support_claim_ids": claims, "counter_claim_ids": [],
                "missing_evidence": (["原文没有营业收入"] if self.false_missing else
                                     ["仍需检查其他同期因素"]),
                "alternative_explanations": ["行业共同变化"]}]}
        elif self.calls == 2:
            answer = {"queries": ["营业收入"]}
        elif self.calls == 3:
            assert payload["search_checks"]
            if self.empty_findings:
                return {"message": {"content": json.dumps({"findings": []})},
                        "usage": {"total_tokens": 100, "prompt_tokens": 60,
                                  "completion_tokens": 40}, "model": self.model}
            answer = {"findings": [{"hypothesis_id": "h1", "kind": (
                "invalid" if self.invalid_counter else
                "contradiction" if self.rounded_conflict or self.self_negating else "alternative"),
                "detail": ("这两项日期不构成事实冲突" if self.self_negating else
                           "仍需核对行业因素"),
                "claim_ids": ["c1", "c2"] if self.rounded_conflict else [],
                "hit_chunk_ids": []}]}
        else:
            answer = {"draft_conclusion": "目前只能讨论候选解释，仍需核查行业因素。",
                      "selected_hypothesis_id": "h1"}
        return {"message": {"content": json.dumps(answer, ensure_ascii=False)},
                "usage": {"total_tokens": 100, "prompt_tokens": 60,
                          "completion_tokens": 40}, "model": self.model}


def sample_stores(tmp_path, *, known_date=True, reprint=False, market_question=False):
    source_path = tmp_path / "source.sqlite3"
    graph_path = tmp_path / "graph.sqlite3"
    debate_path = tmp_path / "debate.sqlite3"
    repo = SQLiteRepository(source_path)
    seed_event_research_subjects(repo)
    citations, claims, sources, document_ids = [], [], [], []
    for index in (1, 2):
        sentence = f"来源{index}记载：贵州茅台于2026年8月18日披露营业收入101亿元。"
        document, chunks = build_document(
            f"<article><p>{sentence}</p></article>".encode("utf-8"), "source.html",
            f"资料{index}", f"发布方{index}", date(2026, 8, 19 + index),
            f"https://example.org/phase5-{index}.html", "announcement" if index == 1 else "news",
            subject_id="600519", source_id=f"source-{index}")
        repo.store_event_document(document, chunks)
        if reprint and index == 2:
            repo.conn.execute("UPDATE documents SET reprint_of=? WHERE document_id=?",
                              (document_ids[0], document.document_id))
            repo.conn.commit()
            document.reprint_of = document_ids[0]
        chunk = chunks[0]
        quote = sentence
        offset = chunk.content.index(quote)
        citations.append(GraphCitation(citation_id=f"citation-{index}",
            document_id=document.document_id, chunk_id=chunk.chunk_id,
            quote=quote, start_offset=offset, end_offset=offset + len(quote)))
        claims.append(GraphClaim(claim_id=f"claim-{index}", event_id="event-1",
            citation_id=f"citation-{index}", attribute="营业收入",
            value_text="101亿元", numeric_status="format_checked", text=sentence))
        sources.append(GraphSource(document_id=document.document_id, source_id=document.source_id,
            source_type=document.document_type, title=document.title, publisher=document.publisher,
            source_url=document.source_url, published_at=document.published_at,
            fetched_at=document.fetched_at, content_hash=document.content_hash,
            reprint_of=document.reprint_of))
        document_ids.append(document.document_id)
    if market_question:
        repo.upsert_company_market([{"subject_id": "600519", "trade_date": "2026-08-20",
            "close": 1200.0, "volume": 100.0, "turnover": 100000.0,
            "source": "test", "source_url": "https://example.org/market",
            "fetched_at": datetime.now(timezone.utc).isoformat()}])
    repo.close()
    graph = EventGraphRun(run_id="graph-phase5", question=(
        "股价变化可能与披露有关吗？" if market_question else "梳理收入披露的证据"),
        subject_code="600519", subject_name="贵州茅台", window_start=START,
        as_of_date=END, status="extracted", planning_status="evidence_collected",
        model="fake-model", planning_tokens=0, extraction_tokens=0,
        estimated_cost_usd=0, created_at=datetime.now(timezone.utc), sources=sources,
        citations=citations, claims=claims, events=[GraphEvent(event_id="event-1",
            event_key="half-year", title="收入披露", event_type="financial_disclosure",
            target="2026年半年度", occurred_at=date(2026, 8, 18) if known_date else None,
            date_status="known" if known_date else "unknown", reported_at=date(2026, 8, 20),
            importance="high", claim_ids=[c.claim_id for c in claims], document_ids=document_ids)])
    store = EventGraphStore(graph_path)
    store.save(graph)
    store.close()
    return source_path, graph_path, debate_path, graph


def test_debate_validates_source_and_persists_independent_report(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    llm = FakeDebateLLM()
    run = EventHypothesisDebate(source, graph_db, debate_db, llm).build(graph.run_id)
    assert llm.calls == 4
    assert run.conclusion.level == "evidence_strong"
    assert run.conclusion.answer_status == "answered"
    assert len(run.hypotheses) == 1 and run.hypotheses[0].support_claim_ids == ["claim-1", "claim-2"]
    assert run.findings[0].kind == "alternative"
    assert run.search_checks[0].hits
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert run.source_database_sha256 == before
    store = EventDebateStore(debate_db, readonly=True)
    try:
        assert store.get(run.run_id).graph_run_id == graph.run_id
        assert store.latest("600519").run_id == run.run_id
        assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


def test_reprint_and_unknown_event_date_degrade_causal_claim(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(
        tmp_path, known_date=False, reprint=True, market_question=True)
    run = EventHypothesisDebate(source, graph_db, debate_db, FakeDebateLLM()).build(graph.run_id)
    assert run.conclusion.level == "cannot_judge"
    assert run.conclusion.answer_status == "partial"
    assert all(h.assessment in {"cannot_judge", "insufficient"} for h in run.hypotheses)
    assert "无法判断" in run.conclusion.text
    assert any("发生日期" in issue for issue in run.quality_issues)


def test_foreign_claim_stops_model_and_preserves_partial_audit(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    llm = FakeDebateLLM(invalid_claim=True)
    run = EventHypothesisDebate(source, graph_db, debate_db, llm).build(graph.run_id)
    assert llm.calls == 1
    assert run.conclusion.answer_status == "insufficient_evidence"
    assert run.stop_reason.startswith("模型研究失败")
    assert not run.hypotheses


def test_reprint_is_one_independent_source_family(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path, reprint=True)
    run = EventHypothesisDebate(source, graph_db, debate_db, FakeDebateLLM()).build(graph.run_id)
    assert run.conclusion.level == "associated"
    assert run.conclusion.answer_status == "partial"
    assert any("一组独立来源" in issue for issue in run.quality_issues)


def test_failed_counter_review_cannot_leave_strong_conclusion(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(invalid_counter=True)).build(graph.run_id)
    assert run.conclusion.level == "insufficient"
    assert run.conclusion.answer_status == "partial"
    assert run.conclusion.selected_hypothesis_id is None
    assert run.stop_reason.startswith("模型研究失败")
    assert run.findings and run.findings[0].kind == "unresolved"
    assert "模型核验未完成" in run.report_draft


def test_equal_numeric_claims_are_not_published_as_a_conflict(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(rounded_conflict=True)).build(graph.run_id)
    assert run.findings[0].kind == "unresolved"
    assert "不能仅据此判为数值冲突" in run.findings[0].detail
    assert any("误判为冲突" in issue for issue in run.quality_issues)


def test_model_cannot_publish_false_absence_or_self_negating_conflict(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(false_missing=True, self_negating=True)).build(graph.run_id)
    assert all("原文没有营业收入" not in text
               for hypothesis in run.hypotheses for text in hypothesis.missing_evidence)
    assert run.findings[0].kind == "scope"
    assert any("取消冲突标签" in issue for issue in run.quality_issues)


def test_all_rejected_model_hypotheses_fall_back_to_graph_facts(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(all_rejected=True)).build(graph.run_id)
    assert run.hypotheses
    assert "图谱原文可直接核对" in run.hypotheses[0].statement
    assert any("保守候选" in issue for issue in run.quality_issues)


def test_malformed_model_candidate_uses_validated_graph_fallback(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(malformed_fields=True)).build(graph.run_id)
    assert run.hypotheses and "图谱原文可直接核对" in run.hypotheses[0].statement
    assert any("字段不完整" in issue for issue in run.quality_issues)


def test_empty_counter_review_falls_back_to_missing_evidence(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = EventHypothesisDebate(source, graph_db, debate_db,
                                FakeDebateLLM(empty_findings=True)).build(graph.run_id)
    assert run.findings
    assert run.findings[0].kind == "unresolved"
    assert any("保守核查项" in issue for issue in run.quality_issues)


def test_partial_graph_caps_hypothesis_strength(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    graph.status = "partial"
    graph.warnings = ["仍有未提取来源"]
    store = EventGraphStore(graph_db)
    store.save(graph)
    store.close()
    run = EventHypothesisDebate(source, graph_db, debate_db, FakeDebateLLM()).build(graph.run_id)
    assert run.hypotheses[0].assessment == "associated"
    assert run.conclusion.level == "associated"
    assert run.conclusion.answer_status == "partial"
    assert "暂不能形成完整判断" in run.conclusion.text


def test_tampered_source_stops_before_model_call(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    repo = SQLiteRepository(source)
    repo.conn.execute("UPDATE document_chunks SET content='被改写的片段' WHERE chunk_id=?",
                      (graph.citations[0].chunk_id,))
    repo.conn.commit()
    repo.close()
    llm = FakeDebateLLM()
    run = EventHypothesisDebate(source, graph_db, debate_db, llm).build(graph.run_id)
    assert llm.calls == 0
    assert run.conclusion.answer_status == "insufficient_evidence"
    assert any("无法回到" in issue for issue in run.quality_issues)


def test_fee_preflight_stops_before_paid_model_call(tmp_path, monkeypatch):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)

    class PaidFakeLLM(FakeDebateLLM):
        model = "paid-test"
        base_url = "https://provider.example"

    monkeypatch.setenv("EVENT_LLM_INPUT_USD_PER_1M", "100")
    monkeypatch.setenv("EVENT_LLM_OUTPUT_USD_PER_1M", "100")
    monkeypatch.setenv("EVENT_DEBATE_MAX_USD_PER_RUN", "0.000001")
    llm = PaidFakeLLM()
    run = EventHypothesisDebate(source, graph_db, debate_db, llm).build(graph.run_id)
    assert llm.calls == 0
    assert run.conclusion.answer_status == "insufficient_evidence"
    assert any("预算" in issue for issue in run.quality_issues)


def test_debate_api_requires_key_and_reads_without_model_call(tmp_path, monkeypatch):
    import app.api.main as api_module

    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    monkeypatch.setattr(api_module, "EventHypothesisDebate",
        lambda source_database, graph_database, debate_database: EventHypothesisDebate(
            source_database, graph_database, debate_database, FakeDebateLLM()))
    app = create_app(write_key="test-key", event_database_path=source,
                     event_graph_path=graph_db, event_debate_path=debate_db)
    with TestClient(app) as client:
        assert client.post("/api/v1/event-research/debate/build",
                           json={"graph_run_id": graph.run_id}).status_code == 401
        assert not debate_db.exists()
        built = client.post("/api/v1/event-research/debate/build",
            json={"graph_run_id": graph.run_id}, headers={"X-API-Key": "test-key"})
        assert built.status_code == 200, built.text
        run_id = built.json()["debate"]["run_id"]
        assert "report_draft" not in built.json()["debate"]
        assert all("snapshot_path" not in source
                   for source in built.json()["debate"]["graph"]["sources"])
        assert client.get("/api/v1/event-research/debate",
            params={"subject_code": "600519"}).json()["debate"]["run_id"] == run_id
        assert client.get(f"/api/v1/event-research/debate/{run_id}").status_code == 200


def test_single_agent_baseline_calls_only_proposer_and_preserves_quote_gate(tmp_path):
    from app.agents.single_event_agent import SingleEventAgent
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    llm = FakeDebateLLM()
    run = SingleEventAgent(source, graph_db, debate_db, llm).build(graph.run_id)
    assert llm.calls == 1
    assert run.hypotheses and not run.findings and not run.search_checks
    assert run.conclusion.answer_status != "answered"
    assert "未运行反证检索" in run.stop_reason
    assert run.graph.run_id == graph.run_id
