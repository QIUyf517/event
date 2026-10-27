"""Source document models shared by event collection and retrieval."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResearchSubject(BaseModel):
    subject_id: str
    subject_type: Literal["company"]
    code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    market: str | None = None
    enabled: bool = True


class Document(BaseModel):
    document_id: str
    title: str
    content: str
    source_url: str
    publisher: str
    published_at: date
    fetched_at: datetime
    document_type: str
    subject_ids: list[str] = Field(default_factory=list)
    source_id: str = ""
    canonical_url: str = ""
    content_hash: str = ""
    snapshot_path: str | None = None
    duplicate_of: str | None = None
    reprint_of: str | None = None


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    title: str
    source_url: str
    published_at: date
    page: int | None = None
    location: str | None = None
    subject_ids: list[str] = Field(default_factory=list)
    source_id: str = ""
    document_type: str = ""
    publisher: str = ""
    content_hash: str = ""


class DocumentStoreResult(BaseModel):
    status: Literal["inserted", "updated", "duplicate"]
    document_id: str
    canonical_document_id: str
    duplicate_reason: Literal["canonical_url", "content_hash", "title", "reprint_of", "none"] = "none"


class ResearchRetrievalResult(BaseModel):
    chunk: DocumentChunk
    score: float
    retrieval_method: Literal["lexical_ngram_metadata_v1"] = "lexical_ngram_metadata_v1"
