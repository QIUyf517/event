import json
from pathlib import Path

from scripts.evaluate_research_methods import aggregate_results, grade, render_markdown_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_semantic_suite_has_twelve_cases_across_four_categories():
    cases = json.loads((PROJECT_ROOT / "evals" / "semantic_cases.json").read_text(encoding="utf-8"))

    assert len(cases) == 12
    assert {case["category"] for case in cases} == {"数字事实", "资料引用", "综合研究", "能力边界"}
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["required_term_groups"] for case in cases)


def test_grade_checks_dates_citations_required_and_forbidden_terms():
    case = {
        "expected_status": "partial",
        "expected_signal": "moderately_positive",
        "expected_market_date": "2026-09-30",
        "expected_share_date": "2026-09-30",
        "expected_document": True,
        "required_term_groups": [["份额"], ["原因", "成因"]],
        "forbidden_terms": ["机构资金流入"],
        "requires_cause_caveat": True,
    }
    answer = {
        "answer_status": "partial",
        "signal": "moderately_positive",
        "source_dates": {"market": "2026-09-30", "shares": "2026-09-30"},
        "direct_answer": "份额有所变化，但现有资料不能证明变化原因。",
        "citations": ["D1"],
    }

    checks = grade(case, answer, [{"citation": "D1"}])

    assert checks["all_checks_passed"] is True
    answer["direct_answer"] = "份额变化说明机构资金流入。"
    failed = grade(case, answer, [{"citation": "D1"}])
    assert failed["forbidden_terms"] is False
    assert failed["cause_caveat"] is False
    assert failed["all_checks_passed"] is False


def test_summary_reports_failures_without_claiming_semantic_correctness():
    case_results = [{
        "case": {"id": "case-1", "category": "能力边界"},
        "methods": [{
            "method": "rules", "latency_ms": 10, "token_usage": 0,
            "checks": {"status": True, "forbidden_terms": False, "all_checks_passed": False},
        }],
    }]
    result = {
        "as_of_date": "2026-09-30", "model_calls_enabled": False,
        "cases": case_results, "summary": aggregate_results(case_results),
    }

    markdown = render_markdown_summary(result)

    assert result["summary"][0]["case_pass_rate"] == 0
    assert "forbidden_terms" in markdown
    assert "不能替代人工语义判断" in markdown
