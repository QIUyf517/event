from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class ETFProfile(BaseModel):
    etf_code: str
    name: str
    fund_company: str | None = None
    index_name: str | None = None
    source: str = "manual_seed"

class MarketBar(BaseModel):
    etf_code: str
    trade_date: date
    close: float | None = None
    volume: float | None = None
    turnover: float | None = None
    source: str
    fetched_at: datetime

class ShareRecord(BaseModel):
    etf_code: str
    trade_date: date
    shares: float | None = None
    source: str
    fetched_at: datetime

class Document(BaseModel):
    document_id: str
    title: str
    content: str
    source_url: str
    publisher: str
    published_at: date
    fetched_at: datetime
    document_type: str
    etf_codes: list[str] = Field(default_factory=list)

class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    title: str
    source_url: str
    published_at: date
    page: int | None = None

class SignalResult(BaseModel):
    etf_code: str
    as_of_date: date
    indicators: dict[str, float | None]
    signal: Literal["positive", "moderately_positive", "neutral", "cautious", "insufficient_data"]
    evidence_strength: Literal["strong", "medium", "weak", "insufficient"]
    missing_fields: list[str] = Field(default_factory=list)
    source_dates: dict[str, date] = Field(default_factory=dict)
    algorithm_version: str

class Evidence(BaseModel):
    evidence_id: str
    evidence_type: Literal["data", "document"]
    title: str
    claim: str
    source_url: str | None = None
    source_date: date | None = None
    details: dict[str, Any] = Field(default_factory=dict)

class ResearchState(BaseModel):
    task_id: str
    question: str
    etf_codes: list[str]
    as_of_date: date
    plan: list[str] = Field(default_factory=list)
    market_results: list[dict[str, Any]] = Field(default_factory=list)
    document_results: list[DocumentChunk] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    final_report: dict[str, Any] | None = None

