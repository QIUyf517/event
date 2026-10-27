"""第一阶段事件研究基准的离线评分器。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.config import PROJECT_ROOT
from app.models.event_research import EventResearchReport


EVENT_CASES_PATH = PROJECT_ROOT / "evals" / "event_research_cases.json"
EVENT_SOURCES_PATH = PROJECT_ROOT / "evals" / "event_source_catalog.json"


class ExpectedEvent(BaseModel):
    event_key: str
    allowed_source_ids: list[str] = Field(min_length=1)


class EventBenchmarkCase(BaseModel):
    id: str
    category: Literal[
        "事件梳理", "公告解读", "跨来源冲突", "候选原因", "证据不足", "未来信息泄漏"
    ]
    subject_code: Literal["600519", "601318", "688981"]
    subject_name: str
    as_of_date: date
    window_days: Literal[30] = 30
    question: str
    scenario: str
    expected_statuses: list[Literal["answered", "partial", "insufficient_evidence", "unsupported"]]
    expected_events: list[ExpectedEvent] = Field(default_factory=list)
    valid_source_ids: list[str] = Field(default_factory=list)
    forbidden_conclusions: list[str] = Field(default_factory=list)
    required_term_groups: list[list[str]] = Field(default_factory=list)
    gold_answer: str
    manual_review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case(self):
        expected_sources = {source for event in self.expected_events for source in event.allowed_source_ids}
        if not expected_sources <= set(self.valid_source_ids):
            raise ValueError("事件允许来源必须包含在题目有效来源中")
        if not self.expected_statuses:
            raise ValueError("每题至少需要一个期望回答状态")
        if not self.gold_answer.strip() and not self.manual_review_notes:
            raise ValueError("每题必须有人工作答或人工评分规则")
        return self


def load_event_source_catalog(path: Path = EVENT_SOURCES_PATH) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources", [])
    result = {item["source_id"]: item for item in sources}
    if len(result) != len(sources):
        raise ValueError("事件来源清单包含重复source_id")
    return result


def load_event_cases(path: Path = EVENT_CASES_PATH,
                     source_path: Path = EVENT_SOURCES_PATH) -> list[EventBenchmarkCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [EventBenchmarkCase.model_validate(item) for item in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("事件评测题ID必须唯一")
    catalog = load_event_source_catalog(source_path)
    missing = sorted({source for case in cases for source in case.valid_source_ids} - set(catalog))
    if missing:
        raise ValueError(f"题目引用了来源清单中不存在的source_id: {', '.join(missing)}")
    return cases


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _all_references_valid(report: dict[str, Any]) -> bool:
    evidence_ids = {item.get("evidence_id") for item in report.get("evidence", [])}
    claim_ids = {item.get("claim_id") for item in report.get("claims", [])}
    if None in evidence_ids or None in claim_ids:
        return False
    for claim in report.get("claims", []):
        refs = claim.get("evidence_ids", [])
        if claim.get("kind") == "fact" and not refs:
            return False
        if not set(refs) <= evidence_ids:
            return False
    for event in report.get("events", []):
        if not set(event.get("evidence_ids", [])) <= evidence_ids:
            return False
        if not set(event.get("claim_ids", [])) <= claim_ids:
            return False
    for hypothesis in report.get("hypotheses", []):
        refs = hypothesis.get("support_evidence_ids", []) + hypothesis.get("counter_evidence_ids", [])
        if not set(refs) <= evidence_ids:
            return False
    judgement = report.get("final_judgement", {})
    return set(judgement.get("supported_claim_ids", [])) <= claim_ids


def _time_boundary_valid(report: dict[str, Any], cutoff: date) -> bool:
    cutoff_dt = datetime.combine(cutoff, datetime.max.time())
    values = [item.get("published_at") for item in report.get("evidence", [])]
    values += [item.get("reported_at") for item in report.get("events", [])]
    values += [item.get("occurred_at") for item in report.get("events", []) if item.get("occurred_at")]
    parsed = [_parse_datetime(value) for value in values]
    return all(value is not None and value <= cutoff_dt for value in parsed)


def _citation_correctness(report: dict[str, Any], case: EventBenchmarkCase,
                          catalog: dict[str, dict[str, Any]]) -> bool:
    evidence = {item.get("evidence_id"): item for item in report.get("evidence", [])}
    valid_sources = set(case.valid_source_ids)
    if any(item.get("source_id") not in valid_sources for item in evidence.values()):
        return False
    for item in evidence.values():
        source = catalog.get(item.get("source_id"))
        if not source or not item.get("excerpt"):
            return False
        host = (urlparse(item.get("source_url", "")).hostname or "").lower()
        allowed_domains = source.get("allowed_domains", [])
        if allowed_domains and not any(host == domain or host.endswith("." + domain)
                                       for domain in allowed_domains):
            return False

    events = {item.get("event_key"): item for item in report.get("events", [])}
    for expected in case.expected_events:
        event = events.get(expected.event_key)
        if not event:
            return False
        cited_sources = {evidence.get(ref, {}).get("source_id")
                         for ref in event.get("evidence_ids", [])}
        if not cited_sources.intersection(expected.allowed_source_ids):
            return False
    return _all_references_valid(report)


def _no_unsupported_assertions(report: dict[str, Any]) -> bool:
    claims = {item.get("claim_id"): item for item in report.get("claims", [])}
    for claim in claims.values():
        if not claim.get("evidence_ids"):
            return False
    supported = report.get("final_judgement", {}).get("supported_claim_ids", [])
    if supported and any(not claims.get(ref, {}).get("evidence_ids") for ref in supported):
        return False
    return True


def score_event_report(case: EventBenchmarkCase | dict[str, Any], report: dict[str, Any],
                       source_catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """对可由程序检查的边界进行评分；语义判断仍需人工复核。"""

    case = case if isinstance(case, EventBenchmarkCase) else EventBenchmarkCase.model_validate(case)
    catalog = source_catalog or load_event_source_catalog()
    try:
        EventResearchReport.model_validate(report)
        contract_valid = True
        contract_error = None
    except ValidationError as exc:
        contract_valid = False
        contract_error = str(exc)

    event_keys = {item.get("event_key") for item in report.get("events", [])}
    text = "\n".join([
        report.get("final_judgement", {}).get("conclusion", ""),
        *(item.get("title", "") + " " + item.get("summary", "") for item in report.get("events", [])),
        *(item.get("text", "") for item in report.get("claims", [])),
        *(item.get("statement", "") for item in report.get("hypotheses", [])),
    ])
    forbidden_hits = [term for term in case.forbidden_conclusions if term in text]
    missing_term_groups = [group for group in case.required_term_groups
                           if not any(term in text for term in group)]
    checks = {
        "contract_valid": contract_valid,
        "subject_scope": report.get("subject_code") == case.subject_code
            and str(report.get("as_of_date")) == case.as_of_date.isoformat(),
        "answer_status": report.get("final_judgement", {}).get("answer_status") in case.expected_statuses,
        "event_coverage": {item.event_key for item in case.expected_events} <= event_keys,
        "time_boundary": _time_boundary_valid(report, case.as_of_date),
        "citation_correctness": _citation_correctness(report, case, catalog),
        "no_unsupported_assertions": _no_unsupported_assertions(report),
        "forbidden_conclusions": not forbidden_hits,
        "required_answer_points": not missing_term_groups,
    }
    return {
        "case_id": case.id,
        "checks": {**checks, "all_machine_checks_passed": all(checks.values())},
        "event_recall": (len({item.event_key for item in case.expected_events} & event_keys) /
                         len(case.expected_events) if case.expected_events else 1.0),
        "forbidden_hits": forbidden_hits,
        "missing_term_groups": missing_term_groups,
        "contract_error": contract_error,
        "manual_review_required": True,
        "manual_review_notes": case.manual_review_notes,
    }


def summarize_event_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(scores)
    return {
        "total": total,
        "fully_passed": sum(item["checks"]["all_machine_checks_passed"] for item in scores),
        "machine_pass_rate": (sum(item["checks"]["all_machine_checks_passed"] for item in scores) /
                              total if total else 0),
        "average_event_recall": (sum(item["event_recall"] for item in scores) / total if total else 0),
        "manual_review_pending": total,
        "limitations": [
            "自动评分只检查结构、引用关系、来源白名单、事件覆盖和时间边界。",
            "引用是否忠实表达原文、推理是否合理，仍须人工逐题审核。",
        ],
    }
