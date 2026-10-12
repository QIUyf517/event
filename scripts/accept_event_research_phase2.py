"""Collect the fixed phase-two public-source sample into an isolated database."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from app.config import PROJECT_ROOT
from app.data.event_sources import canonicalize_url
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.rag.ingest import collect_event_document


DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "event_sources_phase2.json"


def load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    start = date.fromisoformat(payload["window_start"])
    as_of = date.fromisoformat(payload["as_of_date"])
    if start > as_of or (as_of - start).days > 30:
        raise ValueError("验收清单研究窗口必须不超过30天")
    items = payload.get("items", [])
    if {item["subject_id"] for item in items} != {"600519", "601318", "688981"}:
        raise ValueError("验收清单必须覆盖三家公司")
    for item in items:
        published = date.fromisoformat(item["published_at"])
        if not start <= published <= as_of:
            raise ValueError(f"资料发布日期越过验收窗口: {item['url']}")
    return payload


def run_acceptance(manifest_path: Path, output_dir: Path) -> dict:
    manifest = load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = output_dir / "snapshots"
    repo = SQLiteRepository(output_dir / "event_research_phase2.sqlite3")
    collected = []
    try:
        seed_event_research_subjects(repo)
        for item in manifest["items"]:
            metadata = dict(item)
            url, source_id = metadata.pop("url"), metadata.pop("source_id")
            metadata["published_at"] = date.fromisoformat(metadata["published_at"])
            existing = repo.conn.execute("SELECT document_id FROM documents WHERE canonical_url=?",
                (canonicalize_url(url),)).fetchone()
            if existing:
                document = repo.event_document(existing["document_id"])
                storage_status = "reused"
            else:
                document, stored = collect_event_document(repo, url, source_id,
                    snapshot_dir=snapshots, **metadata)
                if stored.status != "inserted":
                    raise AssertionError(f"首次采集未写入独立样本: {url} ({stored.status})")
                storage_status = stored.status
            snapshot = Path(document.snapshot_path)
            if not snapshot.is_file() or sha256(snapshot.read_bytes()).hexdigest() != document.content_hash:
                raise AssertionError(f"原文快照或哈希校验失败: {url}")
            subject = repo.research_subject(item["subject_id"])
            identity_markers = [subject.code, subject.name, *subject.aliases]
            if not any(marker in document.content for marker in identity_markers):
                raise AssertionError(f"正文未定位到研究对象: {url}")
            collected.append({"document_id": document.document_id, "subject_id": item["subject_id"],
                "source_id": source_id, "material_type": document.document_type,
                "published_at": document.published_at.isoformat(), "url": url,
                "content_hash": document.content_hash, "snapshot": str(snapshot),
                "storage_status": storage_status,
                "characters": len(document.content),
                "chunks": repo.conn.execute("SELECT COUNT(*) FROM document_chunks WHERE document_id=?",
                    (document.document_id,)).fetchone()[0]})

        first_document = repo.event_document(collected[0]["document_id"])
        duplicate = repo.store_event_document(first_document, [])
        if duplicate.status != "duplicate" or duplicate.duplicate_reason != "canonical_url":
            raise AssertionError("重复URL未被识别为同一证据")

        as_of = date.fromisoformat(manifest["as_of_date"])
        start = date.fromisoformat(manifest["window_start"])
        per_subject = {}
        for subject_id in ("600519", "601318", "688981"):
            counts = repo.event_material_counts(subject_id)
            types = {row[0] for row in repo.conn.execute(
                "SELECT document_type FROM documents WHERE subject_ids LIKE ?", (f'%"{subject_id}"%',))}
            if counts["documents"] < 2 or counts["chunks"] != counts["metadata_aligned_chunks"]:
                raise AssertionError(f"{subject_id}快照、切片或元数据没有对齐")
            if not {"financial", "news"} <= types:
                raise AssertionError(f"{subject_id}缺少公告财务原文或新闻样本")
            results = repo.search_event_chunks("", subject_id, as_of, start, limit=1000)
            if not results or any(item.chunk.published_at > as_of for item in results):
                raise AssertionError(f"{subject_id}截止日期过滤失败")
            earliest = min(item.chunk.published_at for item in results)
            before = repo.search_event_chunks("", subject_id, earliest - timedelta(days=1), None, limit=1000)
            if before:
                raise AssertionError(f"{subject_id}未来资料进入了较早截止日的检索结果")
            per_subject[subject_id] = {**counts, "material_types": sorted(types),
                "retrieved_chunks": len(results), "latest_published_at": max(
                    item.chunk.published_at for item in results).isoformat()}

        integrity = repo.conn.execute("PRAGMA integrity_check").fetchone()[0]
        result = {"status": "passed", "run_at": datetime.now().astimezone().isoformat(),
            "manifest": str(manifest_path.resolve()), "window_start": start.isoformat(),
            "as_of_date": as_of.isoformat(), "database": str(repo.path.resolve()),
            "database_integrity": integrity, "wrote_existing_etf_database": False,
            "documents": len(collected), "snapshots": len(list(snapshots.iterdir())),
            "per_subject": per_subject, "duplicate_check": duplicate.model_dump(mode="json"),
            "collected": collected}
        (output_dir / "acceptance.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        repo.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="企业事件研究阶段二真实来源验收")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output = args.output_dir or PROJECT_ROOT / "output" / f"event-phase2-{datetime.now():%Y%m%d-%H%M%S}"
    print(json.dumps(run_acceptance(args.manifest, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
