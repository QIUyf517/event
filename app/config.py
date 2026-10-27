"""企业事件研究应用的配置。"""

from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
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

EVENT_RESEARCH_DATABASE_PATH = Path(os.getenv(
    "EVENT_RESEARCH_DATABASE_PATH", PROJECT_ROOT / "data" / "event_research.sqlite3"))
if not EVENT_RESEARCH_DATABASE_PATH.is_absolute():
    EVENT_RESEARCH_DATABASE_PATH = PROJECT_ROOT / EVENT_RESEARCH_DATABASE_PATH

EVENT_GRAPH_DATABASE_PATH = Path(os.getenv(
    "EVENT_GRAPH_DATABASE_PATH", PROJECT_ROOT / "data" / "event_graph.sqlite3"))
if not EVENT_GRAPH_DATABASE_PATH.is_absolute():
    EVENT_GRAPH_DATABASE_PATH = PROJECT_ROOT / EVENT_GRAPH_DATABASE_PATH

EVENT_DEBATE_DATABASE_PATH = Path(os.getenv(
    "EVENT_DEBATE_DATABASE_PATH", PROJECT_ROOT / "data" / "event_debate.sqlite3"))
if not EVENT_DEBATE_DATABASE_PATH.is_absolute():
    EVENT_DEBATE_DATABASE_PATH = PROJECT_ROOT / EVENT_DEBATE_DATABASE_PATH

REQUEST_TIMEOUT = int(os.getenv("EVENT_REQUEST_TIMEOUT", "20"))
API_WRITE_KEY = os.getenv("EVENT_API_WRITE_KEY", "")
API_CORS_ORIGINS = tuple(item.strip() for item in os.getenv(
    "EVENT_API_CORS_ORIGINS", "").split(",") if item.strip())
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "")
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
APP_VERSION = "0.3.0"
