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
    location: str | None = None

class SignalResult(BaseModel):
    etf_code: str
    as_of_date: date
    indicators: dict[str, float | None]
    signal: Literal["positive", "moderately_positive", "neutral", "cautious", "insufficient_data"]
    evidence_strength: Literal["strong", "medium", "weak", "insufficient"]
    missing_fields: list[str] = Field(default_factory=list)
    source_dates: dict[str, date] = Field(default_factory=dict)
    algorithm_version: str
    data_mode: Literal["demo", "live"]
    source_records: dict[str, int] = Field(default_factory=dict)

class Evidence(BaseModel):
    evidence_id: str
    evidence_type: Literal["data", "document"]
    title: str
    claim: str
    source_url: str | None = None
    source_date: date | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    classification: Literal["verified_fact", "calculation", "inference", "unverified"] = "verified_fact"

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
    data_mode: Literal["demo", "live"] = "demo"

class SyncResult(BaseModel):
    sync_id: str
    etf_code: str
    data_mode: Literal["live"] = "live"
    start_date: date
    end_date: date
    market_status: Literal["success", "failed"]
    share_status: Literal["success", "partial", "failed", "unavailable"]
    market_rows: int = 0
    share_rows: int = 0
    warnings: list[str] = Field(default_factory=list)

class ResearchReport(BaseModel):
    title: str = "ETF研究报告"
    data_mode: Literal["demo", "live"]
    etf: dict[str, Any] | None
    as_of_date: date
    data_cutoff_date: date | None = None
    verified_facts: list[dict[str, Any]] = Field(default_factory=list)
    calculated_results: dict[str, Any]
    reasonable_inferences: list[str] = Field(default_factory=list)
    unverified_information: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str

