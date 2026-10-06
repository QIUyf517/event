import importlib.metadata
import json
import sys
from pathlib import Path

from app.config import DATA_MODE, LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL_ID, database_path_for_mode

REQUIRED = ("pydantic", "streamlit", "requests", "beautifulsoup4", "pypdf", "akshare", "numpy", "pandas", "pytest", "fastapi", "uvicorn", "reportlab", "httpx2")

def run_checks() -> dict:
    versions = {}
    missing = []
    for package in REQUIRED:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    db_path = database_path_for_mode()
    result = {
        "ok": not missing and sys.version_info >= (3, 11),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "isolated_venv": Path(sys.prefix).name == ".venv",
        "data_mode": DATA_MODE,
        "database_path": str(db_path),
        "database_parent_exists": db_path.parent.exists(),
        "llm_configured": bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL_ID),
        "llm_enabled": LLM_ENABLED,
        "versions": versions,
        "missing_packages": missing,
    }
    return result

def main():
    result = run_checks()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)

if __name__ == "__main__":
    main()
