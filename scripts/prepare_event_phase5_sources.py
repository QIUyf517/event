"""向新建的 SQLite 备份补入三份公开公告，不修改来源数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT
from app.data.event_sources import canonicalize_url
from app.data.repositories.sqlite import SQLiteRepository
from app.rag.ingest import collect_event_document


SOURCE = PROJECT_ROOT / "output/event-phase3-20261012-acceptance/event_research_phase3.sqlite3"
MANIFEST = PROJECT_ROOT / "data/event_sources_phase5.json"
OUTPUT = PROJECT_ROOT / "output/event-phase5-20261013-sources"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def backup(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    original = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        original.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("资料副本完整性检查失败")
    finally:
        copy.close()
        original.close()


def run(source: Path, manifest_path: Path, output: Path, *, resume: bool = False) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_file() or source == output or source.parent == output or source.parent in output.parents:
        raise ValueError("来源库不存在或输出目录与原库重叠")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    start, end = date.fromisoformat(manifest["window_start"]), date.fromisoformat(manifest["as_of_date"])
    if start > end or (end - start).days > 30:
        raise ValueError("验收窗口无效")
    items = manifest["items"]
    if {item["subject_id"] for item in items} != {"600519", "601318", "688981"}:
        raise ValueError("公告清单没有覆盖三家公司")
    source_hash = digest(source)
    db_copy = output / "event_research_phase5_source.sqlite3"
    audit_path = output / "collection_audit.json"
    if output.exists():
        if not resume or not db_copy.is_file() or not audit_path.is_file():
            raise FileExistsError("目标目录已存在；核对后使用 --resume")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit["source_sha256_before"] != source_hash:
            raise ValueError("原库哈希已变化，拒绝续跑")
    else:
        output.mkdir(parents=True)
        backup(source, db_copy)
        audit = {"started_at": datetime.now(timezone.utc).isoformat(),
                 "source_database": str(source), "source_sha256_before": source_hash,
                 "source_copy": str(db_copy), "items": {}}
    repo = SQLiteRepository(db_copy)
    try:
        for item in items:
            url = item["url"]
            published = date.fromisoformat(item["published_at"])
            if not start <= published <= end:
                raise ValueError(f"公告超出研究窗口：{url}")
            existing = repo.conn.execute("SELECT document_id FROM documents WHERE canonical_url=?",
                                         (canonicalize_url(url),)).fetchone()
            if existing:
                document = repo.event_document(existing["document_id"])
                status = "reused"
            else:
                metadata = {key: item[key] for key in ("title", "publisher", "subject_id")}
                document, stored = collect_event_document(repo, url, item["source_id"],
                    snapshot_dir=output / "snapshots", published_at=published,
                    material_type="announcement", **metadata)
                status = stored.status
                if status != "inserted":
                    raise ValueError(f"公告未独立保存：{url} ({status})")
            snapshot = Path(document.snapshot_path)
            normalized = re.sub(r"\s+", "", document.content)
            if (not snapshot.is_file() or digest(snapshot) != document.content_hash or
                    item["date_marker"] not in normalized or
                    item["subject_id"] not in document.content or
                    document.document_type != "announcement"):
                raise ValueError(f"公告快照、主体或完整事件日期校验失败：{url}")
            audit["items"][item["subject_id"]] = {"url": url,
                "document_id": document.document_id, "published_at": published.isoformat(),
                "event_date_marker": item["date_marker"], "snapshot": str(snapshot),
                "sha256": document.content_hash, "status": status,
                "chunks": repo.conn.execute("SELECT COUNT(*) FROM document_chunks WHERE document_id=?",
                                            (document.document_id,)).fetchone()[0]}
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        audit["database_integrity"] = repo.conn.execute("PRAGMA integrity_check").fetchone()[0]
        audit["foreign_key_violations"] = len(repo.conn.execute("PRAGMA foreign_key_check").fetchall())
        audit["source_sha256_after"] = digest(source)
        audit["source_unchanged"] = audit["source_sha256_after"] == source_hash
        audit["prepared_at"] = datetime.now(timezone.utc).isoformat()
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        if audit["database_integrity"] != "ok" or audit["foreign_key_violations"] or not audit["source_unchanged"]:
            raise RuntimeError("原库哈希或资料副本完整性检查失败")
        return audit
    finally:
        repo.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="在独立副本中保存阶段五前置公告样本")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.manifest, args.output_dir, resume=args.resume),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
