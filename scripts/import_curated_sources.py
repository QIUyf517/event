"""Stage official documents, back up the live DB, then import the batch."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from app.config import ALLOWED_ETFS, PROJECT_ROOT, database_path_for_mode
from app.data.repositories.sqlite import SQLiteRepository
from app.rag.ingest import fetch_official_document

MANIFEST = PROJECT_ROOT / "data" / "curated_sources.json"


def load_manifest(path: Path = MANIFEST):
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("资料清单为空或格式错误")
    for row in rows:
        if row["etf_code"] not in ALLOWED_ETFS or date.fromisoformat(row["published_at"]) > date.today():
            raise ValueError("资料代码或发布日期无效")
    return rows


def stage(rows):
    staged = []
    for row in rows:
        metadata = {"etf_code": row["etf_code"], "title": row["title"],
                    "publisher": row["publisher"],
                    "published_at": date.fromisoformat(row["published_at"]),
                    "document_type": row["document_type"]}
        doc, chunks = fetch_official_document(row["url"], **metadata)
        if row["etf_code"] not in doc.content or not chunks:
            raise ValueError(f"资料正文未确认基金代码或无文本：{row['url']}")
        staged.append((doc, chunks))
    return staged


def main():
    parser = argparse.ArgumentParser(description="导入经过清单核对的上交所资料")
    parser.add_argument("--apply", action="store_true", help="备份后写入真实数据库；省略则仅联网预检")
    args = parser.parse_args()
    staged = stage(load_manifest())
    result = {"staged": [{"etf": doc.etf_codes[0], "title": doc.title,
                         "published_at": doc.published_at.isoformat(),
                         "chunks": len(chunks), "source_url": doc.source_url}
                        for doc, chunks in staged], "wrote_live_database": False}
    if args.apply:
        db_path = database_path_for_mode("live")
        if not db_path.exists():
            raise FileNotFoundError(f"真实数据库不存在：{db_path}")
        backup = db_path.with_name(f"{db_path.stem}.before-ai-docs-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
        repo = SQLiteRepository(db_path)
        try:
            with closing(sqlite3.connect(backup)) as destination:
                repo.conn.backup(destination)
            with closing(sqlite3.connect(backup)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("数据库备份完整性检查失败，已取消导入")
            repo.upsert_documents(staged)
            result.update({"wrote_live_database": True, "backup": str(backup),
                           "documents": repo.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                           "chunks": repo.conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]})
        finally:
            repo.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
