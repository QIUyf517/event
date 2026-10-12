from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.config import PROJECT_ROOT, REQUEST_TIMEOUT
from app.data.event_sources import canonicalize_url, validate_source_url
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import Document, DocumentChunk, DocumentStoreResult

OFFICIAL_DOMAINS = {"sse.com.cn", "www.sse.com.cn", "static.sse.com.cn", "csrc.gov.cn", "www.csrc.gov.cn"}

class DocumentCollector(Protocol):
    def collect(self) -> tuple[Document, list[DocumentChunk]]: ...

def _document_id(payload: bytes, owner_code: str) -> str:
    return f"{owner_code}-{sha256(payload).hexdigest()[:20]}"

def _pdf_chunks(payload: bytes, document_id: str, title: str, source_url: str, published_at: date) -> tuple[str, list[DocumentChunk]]:
    reader = PdfReader(BytesIO(payload))
    chunks, pages = [], []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        pages.append(text)
        for index in range(0, len(text), 800):
            chunks.append(DocumentChunk(chunk_id=f"{document_id}-p{page_number}-c{index // 800 + 1}", document_id=document_id, content=text[index:index + 800], title=title, source_url=source_url, published_at=published_at, page=page_number, location=f"page:{page_number}"))
    if not chunks:
        raise ValueError("PDF未提取到文本；扫描版PDF需要OCR后再导入")
    return "\n\n".join(pages), chunks

def _html_chunks(payload: bytes, document_id: str, title: str, source_url: str, published_at: date) -> tuple[str, list[DocumentChunk]]:
    soup = BeautifulSoup(payload, "html.parser")
    for node in soup(["script", "style", "nav", "footer"]):
        node.decompose()
    article = soup.select_one(".allZoom, article, .article-content, .article_content")
    if article:
        text = "\n".join(" ".join(item.split()) for item in article.stripped_strings if item.strip())
        if not text:
            raise ValueError("HTML公告正文为空")
        chunks = [DocumentChunk(chunk_id=f"{document_id}-article-{index // 800 + 1}", document_id=document_id, content=text[index:index + 800], title=title, source_url=source_url, published_at=published_at, location=f"article:chars:{index + 1}-{min(index + 800, len(text))}") for index in range(0, len(text), 800)]
        return text, chunks
    blocks, chunks = [], []
    current_heading = "正文"
    paragraph_index = 0
    for node in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            continue
        if node.name in {"h1", "h2", "h3"}:
            current_heading = text
            continue
        paragraph_index += 1
        blocks.append(text)
        chunks.append(DocumentChunk(chunk_id=f"{document_id}-para-{paragraph_index}", document_id=document_id, content=text, title=title, source_url=source_url, published_at=published_at, location=f"section:{current_heading};paragraph:{paragraph_index}"))
    if not chunks:
        raise ValueError("HTML未提取到正文段落")
    return "\n".join(blocks), chunks

def build_document(payload: bytes, filename: str, title: str, publisher: str, published_at: date,
                   source_url: str, document_type: str, etf_code: str = "510300",
                   subject_id: str | None = None, source_id: str = "",
                   snapshot_path: str | None = None, reprint_of: str | None = None) -> tuple[Document, list[DocumentChunk]]:
    owner_code = subject_id or etf_code
    document_id = _document_id(payload, owner_code)
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        content, chunks = _pdf_chunks(payload, document_id, title, source_url, published_at)
    elif suffix in {".html", ".htm"}:
        content, chunks = _html_chunks(payload, document_id, title, source_url, published_at)
    else:
        raise ValueError("仅支持PDF和HTML文档")
    digest = sha256(payload).hexdigest()
    subject_ids = [subject_id] if subject_id else []
    canonical_url = canonicalize_url(source_url) if source_url.startswith(("http://", "https://")) else source_url
    document = Document(document_id=document_id, title=title, content=content, source_url=source_url,
        publisher=publisher, published_at=published_at, fetched_at=datetime.utcnow(),
        document_type=document_type, etf_codes=[] if subject_id else [etf_code],
        subject_ids=subject_ids, source_id=source_id, canonical_url=canonical_url,
        content_hash=digest, snapshot_path=snapshot_path, reprint_of=reprint_of)
    chunks = [chunk.model_copy(update={"subject_ids": subject_ids, "source_id": source_id,
        "document_type": document_type, "publisher": publisher, "content_hash": digest}) for chunk in chunks]
    return document, chunks

def ingest_local(repo: SQLiteRepository, path: str | Path, **metadata) -> Document:
    file_path = Path(path)
    payload = file_path.read_bytes()
    source_url = metadata.pop("source_url", "") or file_path.resolve().as_uri()
    document, chunks = build_document(payload, file_path.name, source_url=source_url, **metadata)
    repo.upsert_document(document, chunks)
    return document

def fetch_official_document(url: str, **metadata) -> tuple[Document, list[DocumentChunk]]:
    """Fetch and parse before any database write, so a batch can be validated first."""
    host = (urlparse(url).hostname or "").lower()
    if host not in OFFICIAL_DOMAINS:
        raise ValueError(f"非允许的官方来源域名: {host}")
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 ETFResearchAgent/0.1"})
    response.raise_for_status()
    filename = Path(urlparse(url).path).name or "announcement.html"
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type and not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    elif "html" in content_type and not filename.lower().endswith((".html", ".htm")):
        filename += ".html"
    return build_document(response.content, filename, source_url=url, **metadata)


def ingest_official_url(repo: SQLiteRepository, url: str, **metadata) -> Document:
    document, chunks = fetch_official_document(url, **metadata)
    repo.upsert_document(document, chunks)
    return document


def fetch_event_document(url: str, source_id: str, **metadata) -> tuple[bytes, str, Document, list[DocumentChunk]]:
    """Download and parse an allow-listed event source without writing the database."""
    source = validate_source_url(source_id, url)
    material_type = metadata.pop("material_type", None) or source["source_type"]
    if material_type not in {"announcement", "news", "financial"}:
        raise ValueError("事件资料类型必须是announcement、news或financial")
    if source["source_type"] == "news" and material_type != "news":
        raise ValueError("新闻来源只能保存为news资料")
    response = None
    last_error = None
    for _ in range(3):
        try:
            response = requests.get(url, timeout=(10, max(REQUEST_TIMEOUT, 45)),
                headers={"User-Agent": "Mozilla/5.0 EventResearchAgent/0.2"})
            break
        except requests.RequestException as exc:
            last_error = exc
    if response is None:
        raise last_error
    response.raise_for_status()
    validate_source_url(source_id, getattr(response, "url", url))
    filename = Path(urlparse(url).path).name or "source.html"
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type and not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    elif "html" in content_type and not filename.lower().endswith((".html", ".htm")):
        filename += ".html"
    document, chunks = build_document(response.content, filename, source_url=url,
        source_id=source_id, document_type=material_type, **metadata)
    return response.content, filename, document, chunks


def collect_event_document(repo: SQLiteRepository, url: str, source_id: str,
                           snapshot_dir: str | Path | None = None, **metadata) -> tuple[Document, DocumentStoreResult]:
    """User-triggered collection that preserves the raw response before canonical storage."""
    payload, filename, document, chunks = fetch_event_document(url, source_id, **metadata)
    snapshots = Path(snapshot_dir or PROJECT_ROOT / "data" / "event_snapshots")
    snapshots.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower() or ".bin"
    snapshot = snapshots / f"{document.content_hash}{suffix}"
    if not snapshot.exists():
        snapshot.write_bytes(payload)
    document = document.model_copy(update={"snapshot_path": str(snapshot.resolve())})
    result = repo.store_event_document(document, chunks)
    return document, result
