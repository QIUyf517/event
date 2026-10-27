"""基于已保存企业文档构建的可审计事件时间线。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GraphSource(BaseModel):
    document_id: str
    source_id: str
    source_type: Literal["announcement", "news", "financial"]
    title: str
    publisher: str
    source_url: str
    published_at: date
    fetched_at: datetime
    content_hash: str
    snapshot_path: str | None = None
    reprint_of: str | None = None


class GraphCitation(BaseModel):
    citation_id: str
    document_id: str
    chunk_id: str
    quote: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    page: int | None = None
    location: str | None = None


class GraphClaim(BaseModel):
    claim_id: str
    event_id: str
    citation_id: str
    attribute: str
    value_text: str | None = None
    qualifier: str | None = None
    unit_context: str | None = None
    numeric_status: Literal["not_applicable", "format_checked", "calculation_mismatch"]
    calculation_note: str | None = None
    text: str


class GraphConflict(BaseModel):
    conflict_id: str
    event_id: str
    kind: Literal["number", "occurred_date", "calculation"]
    attribute: str
    variants: list[str] = Field(min_length=2)
    claim_ids: list[str] = Field(min_length=1)


class GraphRelation(BaseModel):
    relation_id: str
    event_id: str
    left_claim_id: str
    right_claim_id: str
    kind: Literal["agreement", "conflict", "reprint", "unresolved"]


class GraphEvent(BaseModel):
    event_id: str
    event_key: str
    title: str
    event_type: Literal["financial_disclosure", "corporate_action", "business_update", "other"]
    target: str
    period_label: str | None = None
    occurred_at: date | None = None
    date_status: Literal["known", "unknown", "conflict"]
    reported_at: date
    importance: Literal["high", "medium", "low"]
    claim_ids: list[str] = Field(min_length=1)
    document_ids: list[str] = Field(min_length=1)
    conflict_ids: list[str] = Field(default_factory=list)


class EventGraphRun(BaseModel):
    schema_version: Literal["event-graph-v1"] = "event-graph-v1"
    run_id: str
    question: str
    subject_code: Literal["600519", "601318", "688981"]
    subject_name: str
    window_start: date
    as_of_date: date
    status: Literal["extracted", "partial", "insufficient_evidence"]
    planning_status: str
    model: str
    planning_tokens: int = Field(ge=0)
    extraction_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    created_at: datetime
    sources: list[GraphSource] = Field(default_factory=list)
    citations: list[GraphCitation] = Field(default_factory=list)
    claims: list[GraphClaim] = Field(default_factory=list)
    events: list[GraphEvent] = Field(default_factory=list)
    conflicts: list[GraphConflict] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self):
        if self.window_start > self.as_of_date or (self.as_of_date - self.window_start).days > 30:
            raise ValueError("事件图谱的研究日期范围无效")
        collections = (("来源", self.sources, "document_id"),
                       ("引用", self.citations, "citation_id"),
                       ("主张", self.claims, "claim_id"),
                       ("事件", self.events, "event_id"),
                       ("冲突", self.conflicts, "conflict_id"),
                       ("来源关系", self.relations, "relation_id"))
        for label, items, field in collections:
            ids = [getattr(item, field) for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label}编号重复")
        sources = {item.document_id: item for item in self.sources}
        citations = {item.citation_id: item for item in self.citations}
        claims = {item.claim_id: item for item in self.claims}
        events = {item.event_id: item for item in self.events}
        conflicts = {item.conflict_id: item for item in self.conflicts}
        if any(not self.window_start <= source.published_at <= self.as_of_date
               for source in self.sources):
            raise ValueError("图谱来源超出研究日期")
        for citation in self.citations:
            if citation.document_id not in sources or citation.end_offset <= citation.start_offset:
                raise ValueError("引用没有有效的来源或原文位置")
        for claim in self.claims:
            if claim.event_id not in events or claim.citation_id not in citations:
                raise ValueError("主张没有有效的事件或引用")
        for event in self.events:
            if not set(event.claim_ids) <= claims.keys() or not set(event.document_ids) <= sources.keys():
                raise ValueError("事件引用了不存在的主张或来源")
            if not set(event.conflict_ids) <= conflicts.keys():
                raise ValueError("事件引用了不存在的冲突")
            if event.date_status == "known" and event.occurred_at is None:
                raise ValueError("已知发生日期的事件必须有日期")
            if event.date_status != "known" and event.occurred_at is not None:
                raise ValueError("未确定发生日期的事件不得显示确定日期")
            for claim_id in event.claim_ids:
                claim = claims[claim_id]
                if claim.event_id != event.event_id or citations[claim.citation_id].document_id not in event.document_ids:
                    raise ValueError("事件、主张和来源之间的关系不一致")
        for conflict in self.conflicts:
            if conflict.event_id not in events or not set(conflict.claim_ids) <= claims.keys():
                raise ValueError("冲突引用了不存在的事件或主张")
        for relation in self.relations:
            if (relation.event_id not in events or relation.left_claim_id not in claims
                    or relation.right_claim_id not in claims
                    or relation.left_claim_id == relation.right_claim_id
                    or claims[relation.left_claim_id].event_id != relation.event_id
                    or claims[relation.right_claim_id].event_id != relation.event_id):
                raise ValueError("来源关系引用了不一致的事件或主张")
        return self
