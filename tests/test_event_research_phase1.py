from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from app.evaluation.event_research import (
    load_event_cases,
    load_event_source_catalog,
    score_event_report,
    summarize_event_scores,
)
from app.models.event_research import EventResearchReport


def valid_timeline_report():
    return {
        "schema_version": "event-research-v1",
        "research_id": "research-001",
        "question": "梳理贵州茅台最近30天的重要事件并按发生时间排序。",
        "subject_code": "600519",
        "subject_name": "贵州茅台",
        "as_of_date": "2026-09-30",
        "window_start": "2026-09-01",
        "plan": {
            "subject_code": "600519", "as_of_date": "2026-09-30",
            "window_start": "2026-09-01", "stop_condition": "两个一手来源均已核对",
            "steps": [{
                "step_id": "p1", "objective": "核对公告与公司资料", "tool": "fixture_search",
                "depends_on": [], "completion_condition": "找到两项事件",
                "failure_fallback": "标记资料不足",
            }],
        },
        "evidence": [
            {
                "evidence_id": "ev-dividend", "source_id": "sse_announcement",
                "source_type": "announcement", "title": "分红方案公告",
                "publisher": "上海证券交易所", "source_url": "https://www.sse.com.cn/example/1",
                "published_at": "2026-09-08T10:00:00", "excerpt": "公司披露分红方案。",
                "stance": "supports",
            },
            {
                "evidence_id": "ev-operation", "source_id": "company_ir_600519",
                "source_type": "announcement", "title": "生产经营更新",
                "publisher": "贵州茅台", "source_url": "https://www.moutaichina.com/example/2",
                "published_at": "2026-09-21T10:00:00", "excerpt": "公司披露生产经营更新。",
                "stance": "supports",
            },
        ],
        "claims": [
            {"claim_id": "c1", "text": "9月8日披露分红方案。", "kind": "fact",
             "evidence_ids": ["ev-dividend"]},
            {"claim_id": "c2", "text": "9月21日披露生产经营更新。", "kind": "fact",
             "evidence_ids": ["ev-operation"]},
        ],
        "events": [
            {
                "event_id": "event-1", "event_key": "dividend_plan", "title": "分红方案",
                "occurred_at": "2026-09-08T10:00:00", "reported_at": "2026-09-08T10:00:00",
                "summary": "9月8日披露分红方案。", "importance": "high",
                "claim_ids": ["c1"], "evidence_ids": ["ev-dividend"],
            },
            {
                "event_id": "event-2", "event_key": "operation_update", "title": "生产经营更新",
                "occurred_at": "2026-09-21T10:00:00", "reported_at": "2026-09-21T10:00:00",
                "summary": "9月21日披露生产经营信息。", "importance": "medium",
                "claim_ids": ["c2"], "evidence_ids": ["ev-operation"],
            },
        ],
        "hypotheses": [],
        "final_judgement": {
            "answer_status": "answered", "conclusion": "两项事件已按日期列出。",
            "confidence": "high", "supported_claim_ids": ["c1", "c2"], "limitations": [],
        },
        "unknowns": [],
        "execution_trace": [{
            "step_id": "p1", "agent": "fixture-agent", "status": "success",
            "started_at": "2026-09-30T10:00:00", "finished_at": "2026-09-30T10:00:01",
            "tool_calls": ["fixture_search"], "evidence_ids": ["ev-dividend", "ev-operation"],
            "stop_reason": "满足完成条件",
        }],
    }


def test_event_contract_rejects_broken_references_and_long_window():
    report = valid_timeline_report()
    report["claims"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="不存在的证据"):
        EventResearchReport.model_validate(report)

    report = valid_timeline_report()
    report["window_start"] = "2026-08-01"
    report["plan"]["window_start"] = "2026-08-01"
    with pytest.raises(ValidationError, match="不能超过30天"):
        EventResearchReport.model_validate(report)


def test_twenty_human_cases_cover_six_categories_and_all_annotations():
    cases = load_event_cases()

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    assert {case.category for case in cases} == {
        "事件梳理", "公告解读", "跨来源冲突", "候选原因", "证据不足", "未来信息泄漏"
    }
    assert {case.subject_code for case in cases} == {"600519", "601318", "688981"}
    assert all(case.gold_answer or case.manual_review_notes for case in cases)
    assert all(case.valid_source_ids and case.forbidden_conclusions for case in cases)
    assert all(case.as_of_date == date(2026, 9, 30) and case.window_days == 30 for case in cases)


def test_source_catalog_does_not_claim_real_collection_is_complete():
    sources = load_event_source_catalog()

    assert len(sources) >= 8
    assert all(item["verification_status"] in {
        "policy_candidate_unverified", "synthetic_fixture_only", "verified_live_2026-10-12"
    } for item in sources.values())


def test_event_scorer_accepts_a_fully_grounded_timeline():
    case = next(case for case in load_event_cases() if case.id == "timeline_600519_two_events")
    result = score_event_report(case, valid_timeline_report())

    assert result["checks"]["all_machine_checks_passed"] is True
    assert result["event_recall"] == 1
    assert result["manual_review_required"] is True


def test_event_scorer_detects_future_source_missing_citation_and_forbidden_claim():
    case = next(case for case in load_event_cases() if case.id == "timeline_600519_two_events")
    report = deepcopy(valid_timeline_report())
    report["evidence"][0]["published_at"] = "2026-10-01T10:00:00"
    report["claims"][0]["evidence_ids"] = []
    report["final_judgement"]["conclusion"] = "保证股价上涨"

    result = score_event_report(case, report)

    assert result["checks"]["contract_valid"] is False
    assert result["checks"]["time_boundary"] is False
    assert result["checks"]["citation_correctness"] is False
    assert result["checks"]["no_unsupported_assertions"] is False
    assert result["checks"]["forbidden_conclusions"] is False
    assert result["checks"]["all_machine_checks_passed"] is False


def test_event_score_summary_preserves_manual_review_boundary():
    case = next(case for case in load_event_cases() if case.id == "timeline_600519_two_events")
    score = score_event_report(case, valid_timeline_report())
    summary = summarize_event_scores([score])

    assert summary["machine_pass_rate"] == 1
    assert summary["manual_review_pending"] == 1
    assert "仍须人工逐题审核" in summary["limitations"][1]
