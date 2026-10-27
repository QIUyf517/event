"""Build React and serve a self-contained, read-only historical demo."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.skip_build:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise RuntimeError("请先安装Node.js和npm")
        if not (ROOT / "frontend/node_modules").is_dir():
            subprocess.run([npm, "ci"], cwd=ROOT / "frontend", check=True)
        subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    demo_dir = ROOT / "output/event-phase6-demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    # Set before importing app.config. Demo can never make a paid model call.
    os.environ.update(LLM_ENABLED="false", EVENT_DEMO_MODE="true",
        EVENT_RESEARCH_DATABASE_PATH=str(demo_dir / "sources.sqlite3"),
        EVENT_GRAPH_DATABASE_PATH=str(demo_dir / "graphs.sqlite3"),
        EVENT_DEBATE_DATABASE_PATH=str(demo_dir / "debates.sqlite3"))
    from app.agents.research_workflow import WorkflowStore
    from app.data.repositories.sqlite import SQLiteRepository
    from app.data.repositories.event_graph import EventGraphStore
    from app.data.repositories.event_debate import EventDebateStore
    from app.data.seed import seed_event_research_subjects
    from app.models.event_graph import EventGraphRun
    from app.models.event_research import EventDebateRun
    repo = SQLiteRepository(demo_dir / "sources.sqlite3")
    seed_event_research_subjects(repo)
    repo.close()
    graph_store = EventGraphStore(demo_dir / "graphs.sqlite3")
    debate_store = EventDebateStore(demo_dir / "debates.sqlite3")
    workflow = WorkflowStore(demo_dir / "debates.runs.sqlite3")
    manifest = json.loads((ROOT / "data/event_demo.json").read_text(encoding="utf-8"))
    for run in manifest["runs"]:
        graph_store.save(EventGraphRun.model_validate(run["graph"]))
        debate_store.save(EventDebateRun.model_validate(run["debate"]))
        workflow.save(run)
    graph_store.close()
    debate_store.close()
    print(f"Historical read-only demo: http://127.0.0.1:{args.port}/research", flush=True)
    if not args.prepare_only:
        import uvicorn
        uvicorn.run("app.api.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
