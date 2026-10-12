"""Typed contracts for the AI-native company event research workflow.

Phase one defines the boundary between source facts, model inferences and the
final judgement.  Collection, persistence and orchestration are intentionally
left to later phases.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ResearchAnswerStatus = Literal["answered", "partial", "insufficient_evidence", "unsupported"]
EvidenceStance = Literal["supports", "opposes", "context"]


class EventResearchPlanStep(BaseModel):
    step_id: str
    objective: str
    tool: str
    depends_on: list[str] = Field(default_factory=list)
    completion_condition: str
    failure_fallback: str
    query: str = ""
    selection_reason: str = ""
    min_results: int = Field(default=1, ge=0, le=10)
    on_failure: Literal["continue", "stop"] = "continue"


class EventResearchPlan(BaseModel):
    subject_code: str
    as_of_date: date
    window_start: date
    steps: list[EventResearchPlanStep] = Field(default_factory=list)
    stop_condition: str

    @model_validator(mode="after")
    def validate_window_and_dependencies(self):
        if self.window_start > self.as_of_date:
            raise ValueError("研究窗口开始日期不能晚于截止日期")
        if (self.as_of_date - self.window_start).days > 30:
            raise ValueError("第一版研究窗口不能超过30天")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("研究计划步骤ID必须唯一")
        known = set(step_ids)
        for step in self.steps:
            if step.step_id in step.depends_on or not set(step.depends_on) <= known:
                raise ValueError("研究计划包含无效依赖")
        dependencies = {step.step_id: step.depends_on for step in self.steps}
        visiting, visited = set(), set()

        def visit(step_id: str):
            if step_id in visiting:
                raise ValueError("研究计划包含循环依赖")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self


class EventEvidence(BaseModel):
    evidence_id: str
    source_id: str
    source_type: Literal["announcement", "news", "financial", "market"]
    title: str
    publisher: str
    source_url: str
    published_at: datetime
    excerpt: str = Field(min_length=1)
    stance: EvidenceStance = "context"


class EventClaim(BaseModel):
    claim_id: str
    text: str
    kind: Literal["fact", "inference"]
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchEvent(BaseModel):
    event_id: str
    event_key: str
    title: str
    occurred_at: datetime | None = None
    reported_at: datetime
    summary: str
    importance: Literal["high", "medium", "low"]
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchHypothesis(BaseModel):
    hypothesis_id: str
    statement: str
    support_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    assessment: Literal["strong", "associated", "insufficient", "rejected"]


class EventFinalJudgement(BaseModel):
    answer_status: ResearchAnswerStatus
    conclusion: str
    confidence: Literal["high", "medium", "low", "none"]
    supported_claim_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EventResearchTrace(BaseModel):
    step_id: str
    agent: str
    status: Literal["success", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime
    tool_calls: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    error: str | None = None


class EventResearchReport(BaseModel):
    """Complete output contract evaluated by the phase-one benchmark."""

    schema_version: Literal["event-research-v1"] = "event-research-v1"
    research_id: str
    question: str
    subject_code: Literal["600519", "601318", "688981"]
    subject_name: str
    as_of_date: date
    window_start: date
    plan: EventResearchPlan
    events: list[ResearchEvent] = Field(default_factory=list)
    evidence: list[EventEvidence] = Field(default_factory=list)
    claims: list[EventClaim] = Field(default_factory=list)
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list, max_length=3)
    final_judgement: EventFinalJudgement
    unknowns: list[str] = Field(default_factory=list)
    execution_trace: list[EventResearchTrace] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references_and_scope(self):
        if self.window_start > self.as_of_date:
            raise ValueError("研究窗口开始日期不能晚于截止日期")
        if (self.as_of_date - self.window_start).days > 30:
            raise ValueError("第一版研究窗口不能超过30天")
        if (self.plan.subject_code, self.plan.as_of_date, self.plan.window_start) != (
                self.subject_code, self.as_of_date, self.window_start):
            raise ValueError("研究计划与报告范围不一致")

        evidence_ids = [item.evidence_id for item in self.evidence]
        claim_ids = [item.claim_id for item in self.claims]
        event_ids = [item.event_id for item in self.events]
        for label, values in (("证据", evidence_ids), ("主张", claim_ids), ("事件", event_ids)):
            if len(values) != len(set(values)):
                raise ValueError(f"{label}ID必须唯一")

        evidence_set, claim_set = set(evidence_ids), set(claim_ids)
        for claim in self.claims:
            if claim.kind == "fact" and not claim.evidence_ids:
                raise ValueError("事实主张必须引用证据")
            if not set(claim.evidence_ids) <= evidence_set:
                raise ValueError("主张引用了不存在的证据")
        for event in self.events:
            if not set(event.claim_ids) <= claim_set or not set(event.evidence_ids) <= evidence_set:
                raise ValueError("事件引用了不存在的主张或证据")
        for hypothesis in self.hypotheses:
            refs = set(hypothesis.support_evidence_ids + hypothesis.counter_evidence_ids)
            if not refs <= evidence_set:
                raise ValueError("假设引用了不存在的证据")
        if not set(self.final_judgement.supported_claim_ids) <= claim_set:
            raise ValueError("最终判断引用了不存在的主张")
        return self
