"""对第五阶段结构化解释及面向用户的表述进行回归检查。"""
import json
from datetime import datetime, timezone

from app.agents.typed_hypothesis import TypedEventHypothesisDebate
from app.data.repositories.event_debate import EventDebateStore
from app.reporting.event_report import public_debate
from tests.test_event_debate_phase5 import sample_stores
from scripts.recheck_typed_event_phase5 import digest, review_packet
from scripts.verify_typed_event_review import verify_case


class TypedFakeLLM:
    configured = True
    model = "fake-model"

    def __init__(self, *, injected=False, gap_with_support=False):
        self.calls = 0
        self.injected = injected
        self.gap_with_support = gap_with_support

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            answer = {"hypotheses": [{
                "interpretation_kind": "event_candidate",
                "support_claim_ids": ["c1"], "counter_claim_ids": [],
                "alternative_kinds": ["industry_factor"],
            }]}
            if self.injected:
                answer["hypotheses"][0]["statement"] = "已于错误日期完成，股价必然上涨"
            if self.gap_with_support:
                answer["hypotheses"][0]["interpretation_kind"] = "evidence_gap"
        elif self.calls == 2:
            answer = {"queries": ["营业收入"]}
        elif self.calls == 3:
            answer = {"findings": [{"hypothesis_id": "h1", "kind": "alternative",
                                   "claim_ids": [], "hit_chunk_ids": []}]}
        else:
            answer = {"selected_hypothesis_id": "h1", "reason_codes": []}
        return {"message": {"content": json.dumps(answer, ensure_ascii=False)},
                "usage": {"total_tokens": 100, "prompt_tokens": 60,
                          "completion_tokens": 40}, "model": self.model}


def test_typed_report_uses_distinct_graph_dates_and_remains_pending(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path, known_date=False)
    run = TypedEventHypothesisDebate(source, graph_db, debate_db, TypedFakeLLM()).build(graph.run_id)
    assert run.interpretation_schema == "typed_selection_v1"
    assert run.hypotheses[0].interpretation_kind == "event_candidate"
    public = public_debate(run)
    statement = public["hypotheses"][0]["statement"]
    assert "发生日未确认" in statement
    assert "所引资料发布日期2026-08-20" in statement
    assert "发生日2026-08-20" not in statement
    assert public["publication_mode"] == "typed_evidence_pending_review"
    assert public["conclusion"]["selected_hypothesis_id"] is None
    assert all(check["query"].startswith("反方原文检索")
               for check in public["search_checks"])
    store = EventDebateStore(debate_db, readonly=True)
    try:
        assert store.get(run.run_id).interpretation_schema == "typed_selection_v1"
    finally:
        store.close()


def test_typed_model_prose_is_discarded_and_never_public(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = TypedEventHypothesisDebate(source, graph_db, debate_db,
                                      TypedFakeLLM(injected=True)).build(graph.run_id)
    assert run.hypotheses[0].interpretation_kind == "evidence_gap"
    assert any("结构化候选字段无效" in issue for issue in run.quality_issues)
    public = public_debate(run)
    assert "已于错误日期完成" not in json.dumps(public, ensure_ascii=False)
    assert "模型未产生有效" not in json.dumps(public, ensure_ascii=False)


def test_evidence_gap_does_not_claim_company_event_support(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = TypedEventHypothesisDebate(source, graph_db, debate_db,
                                      TypedFakeLLM(gap_with_support=True)).build(graph.run_id)
    assert run.hypotheses[0].interpretation_kind == "evidence_gap"
    assert run.hypotheses[0].support_claim_ids == []
    assert any("非事件方向" in issue for issue in run.quality_issues)


def test_manual_review_is_bound_to_exact_saved_reports(tmp_path):
    source, graph_db, debate_db, graph = sample_stores(tmp_path)
    run = TypedEventHypothesisDebate(source, graph_db, debate_db, TypedFakeLLM()).build(graph.run_id)
    public = public_debate(run)
    debate_file, public_file = tmp_path / "debate_moutai.json", tmp_path / "public_moutai.json"
    debate_file.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    public_file.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    decision = review_packet(run, public, debate_sha256=digest(debate_file),
                             public_sha256=digest(public_file))
    decision.update(status="completed", reviewer="Test reviewer",
                    reviewed_at=datetime.now(timezone.utc).isoformat(),
                    overall_verdict="pass")
    for row in decision["hypotheses"] + decision["findings"]:
        row["semantic_verdict"] = "pass"
    (tmp_path / "review_moutai.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8")
    assert verify_case(tmp_path, "moutai")["valid"]
    public_file.write_text(public_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert not verify_case(tmp_path, "moutai")["valid"]
