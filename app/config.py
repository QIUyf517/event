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
ALLOWED_ETFS = {"510300": "沪深300ETF"}
ALGORITHM_VERSION = "v0.1"

