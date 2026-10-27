"""以大模型为核心的企业事件研究流程所使用的结构化数据契约。

第一阶段定义来源事实、模型推断与最终判断之间的边界。
资料采集、持久化和流程编排由后续阶段实现。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.event_graph import EventGraphRun


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
    """供第一阶段基准评测使用的完整输出契约。"""

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


class DebateHypothesis(BaseModel):
    hypothesis_id: str
    statement: str = Field(min_length=1, max_length=350)
    interpretation_kind: Literal["event_candidate", "industry_factor", "market_factor", "evidence_gap"] | None = None
    alternative_kinds: list[Literal["other_company_event", "industry_factor", "market_factor", "unknown"]] = Field(default_factory=list)
    support_claim_ids: list[str] = Field(default_factory=list)
    counter_claim_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    assessment: Literal["evidence_strong", "associated", "insufficient", "cannot_judge"]


class DebateSearchHit(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    title: str
    published_at: date
    excerpt: str


class DebateSearchCheck(BaseModel):
    query: str
    hits: list[DebateSearchHit] = Field(default_factory=list)


class DebateFinding(BaseModel):
    hypothesis_id: str
    kind: Literal["chronology", "contradiction", "source_dependence", "omission",
                  "scope", "alternative", "unresolved"]
    detail: str = Field(min_length=1, max_length=500)
    claim_ids: list[str] = Field(default_factory=list)
    hit_chunk_ids: list[str] = Field(default_factory=list)


class DebateConclusion(BaseModel):
    level: Literal["evidence_strong", "associated", "insufficient", "cannot_judge"]
    answer_status: ResearchAnswerStatus
    text: str
    selected_hypothesis_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
    reason_codes: list[Literal["date_unknown", "numeric_conflict", "source_dependence", "market_missing", "counter_unresolved", "other"]] = Field(default_factory=list)


class EventDebateRun(BaseModel):
    schema_version: Literal["event-debate-v1"] = "event-debate-v1"
    run_id: str
    graph_run_id: str
    graph: EventGraphRun
    source_database_sha256: str = ""
    created_at: datetime
    model: str
    model_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    stop_reason: str
    hypotheses: list[DebateHypothesis] = Field(default_factory=list, max_length=3)
    search_checks: list[DebateSearchCheck] = Field(default_factory=list, max_length=3)
    findings: list[DebateFinding] = Field(default_factory=list)
    conclusion: DebateConclusion
    quality_issues: list[str] = Field(default_factory=list)
    report_draft: str = ""
    interpretation_schema: Literal["legacy_free_text", "typed_selection_v1"] = "legacy_free_text"

    @model_validator(mode="after")
    def validate_debate_references(self):
        if self.graph_run_id != self.graph.run_id:
            raise ValueError("研究报告引用了其他图谱运行")
        claim_ids = {item.claim_id for item in self.graph.claims}
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("候选解释编号重复")
        hit_ids = {hit.chunk_id for check in self.search_checks for hit in check.hits}
        for hypothesis in self.hypotheses:
            if not set(hypothesis.support_claim_ids + hypothesis.counter_claim_ids) <= claim_ids:
                raise ValueError("候选解释引用了当前图谱之外的主张")
        for finding in self.findings:
            if finding.hypothesis_id not in hypothesis_ids:
                raise ValueError("反方意见引用了不存在的解释")
            if not set(finding.claim_ids) <= claim_ids or not set(finding.hit_chunk_ids) <= hit_ids:
                raise ValueError("反方意见引用了未经保存的证据")
        if (self.conclusion.selected_hypothesis_id is not None
                and self.conclusion.selected_hypothesis_id not in hypothesis_ids):
            raise ValueError("最终判断引用了不存在的解释")
        for check in self.search_checks:
            if any(not self.graph.window_start <= hit.published_at <= self.graph.as_of_date
                   for hit in check.hits):
                raise ValueError("反方检索包含截止日之外的资料")
        if self.interpretation_schema == "typed_selection_v1":
            if any(h.interpretation_kind is None for h in self.hypotheses):
                raise ValueError("结构化解释缺少候选类型")
            if any(h.interpretation_kind == "event_candidate" and not h.support_claim_ids
                   for h in self.hypotheses):
                raise ValueError("事件候选必须有图谱支持主张")
        return self
