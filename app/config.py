from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _load_env_file(path: Path) -> None:
    """加载简单 KEY=VALUE 配置；已存在的系统环境变量优先。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)

_load_env_file(PROJECT_ROOT / ".env")
DATABASE_PATH = Path(os.getenv("ETF_DATABASE_PATH", PROJECT_ROOT / "data" / "etf_research.sqlite3"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH
DATA_MODE = os.getenv("ETF_DATA_MODE", "demo").lower()
if DATA_MODE not in {"demo", "live"}:
    raise ValueError("ETF_DATA_MODE 只能是 demo 或 live")
REQUEST_TIMEOUT = int(os.getenv("ETF_REQUEST_TIMEOUT", "20"))
MAX_SHARE_DATES = int(os.getenv("ETF_MAX_SHARE_DATES", "30"))
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "")
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
ALLOWED_ETFS = {"510300": "沪深300ETF"}
ALGORITHM_VERSION = "v0.1"

def database_path_for_mode(mode: str | None = None) -> Path:
    selected = (mode or DATA_MODE).lower()
    if selected not in {"demo", "live"}:
        raise ValueError("数据模式只能是 demo 或 live")
    suffix = DATABASE_PATH.suffix or ".sqlite3"
    stem = DATABASE_PATH.stem
    if stem.endswith(".demo") or stem.endswith(".live"):
        stem = stem.rsplit(".", 1)[0]
    return DATABASE_PATH.with_name(f"{stem}.{selected}{suffix}")

