from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.config import PROJECT_ROOT


DEFAULT_CATALOG_PATH = PROJECT_ROOT / "evals" / "event_source_catalog.json"


@lru_cache(maxsize=4)
def load_source_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = payload.get("sources", [])
    result = {item["source_id"]: item for item in sources}
    if len(result) != len(sources):
        raise ValueError("事件来源清单包含重复source_id")
    return result


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("资料URL必须是完整的HTTP或HTTPS地址")
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host + port, path, parts.query, ""))


def validate_source_url(source_id: str, url: str,
                        catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    source = (catalog or load_source_catalog()).get(source_id)
    if not source:
        raise ValueError(f"来源不在准入清单中: {source_id}")
    host = (urlsplit(canonicalize_url(url)).hostname or "").lower()
    allowed = [item.lower() for item in source.get("allowed_domains", [])]
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ValueError(f"URL域名{host}不属于来源{source_id}的准入范围")
    if source.get("verification_status") == "synthetic_fixture_only":
        raise ValueError(f"来源{source_id}只允许用于合成评测，不能联网采集")
    return source
