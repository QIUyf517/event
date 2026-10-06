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
API_WRITE_KEY = os.getenv("ETF_API_WRITE_KEY", "")
API_CORS_ORIGINS = tuple(item.strip() for item in os.getenv("ETF_API_CORS_ORIGINS", "").split(",") if item.strip())
PDF_FONT_PATH = os.getenv("ETF_PDF_FONT_PATH", "")
SETTINGS_DATABASE_PATH = Path(os.getenv("ETF_SETTINGS_DATABASE_PATH", PROJECT_ROOT / "data" / "app_settings.sqlite3"))
if not SETTINGS_DATABASE_PATH.is_absolute():
    SETTINGS_DATABASE_PATH = PROJECT_ROOT / SETTINGS_DATABASE_PATH
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "")
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
ETF_CATALOG = {
    "510300": {"name": "沪深300ETF", "fund_company": "华泰柏瑞基金管理有限公司", "index_name": "沪深300指数"},
    "510050": {"name": "华夏上证50ETF", "fund_company": "华夏基金管理有限公司", "index_name": "上证50指数"},
    "510500": {"name": "南方中证500ETF", "fund_company": "南方基金管理股份有限公司", "index_name": "中证500指数"},
}
ALLOWED_ETFS = {code: item["name"] for code, item in ETF_CATALOG.items()}
ALGORITHM_VERSION = "v0.2"
APP_VERSION = "0.2.0"

def database_path_for_mode(mode: str | None = None) -> Path:
    selected = (mode or DATA_MODE).lower()
    if selected not in {"demo", "live"}:
        raise ValueError("数据模式只能是 demo 或 live")
    suffix = DATABASE_PATH.suffix or ".sqlite3"
    stem = DATABASE_PATH.stem
    if stem.endswith(".demo") or stem.endswith(".live"):
        stem = stem.rsplit(".", 1)[0]
    return DATABASE_PATH.with_name(f"{stem}.{selected}{suffix}")

