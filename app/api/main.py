"""提供限定范围内企业事件研究的 HTTP API。"""

from __future__ import annotations

import secrets
import os
import json
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agents.event_graph_builder import EventGraphBuilder
from app.agents.event_orchestrator import EventResearchOrchestrator
from app.config import (API_CORS_ORIGINS, API_WRITE_KEY, APP_VERSION,
                        EVENT_DEBATE_DATABASE_PATH, EVENT_GRAPH_DATABASE_PATH, EVENT_RESEARCH_DATABASE_PATH,
                        PROJECT_ROOT)
from app.agents.typed_hypothesis import TypedEventHypothesisDebate as EventHypothesisDebate
from app.data.repositories.event_debate import EventDebateStore
from app.data.repositories.event_graph import EventGraphStore
from app.data.repositories.sqlite import SQLiteRepository
from app.tools.research_tools import EVENT_SUBJECTS
from app.agents.research_workflow import WorkflowStore, new_run, execute_run
from app.reporting.event_report import public_debate


EventMaterialType = Literal["announcement", "news", "financial"]
ACCEPTED_EVENT_SAMPLE_PATH = (PROJECT_ROOT / "output" / "event-phase2-20261012-accepted"
                              / "event_research_phase2.sqlite3")
ACCEPTED_EVENT_PHASE3_PATH = (PROJECT_ROOT / "output" / "event-phase3-20261012-acceptance"
                              / "event_research_phase3.sqlite3")


def event_excerpt(content: str, query: str, max_chars: int = 320) -> str:
    text = " ".join(content.split())
    position = text.lower().find(query.lower())
    start = max(0, position - 90) if position >= 0 else 0
    end = min(len(text), start + max_chars)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


class EventPlanRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    subject_code: str
    window_start: date
    as_of_date: date


class EventDebateRequest(BaseModel):
    graph_run_id: str = Field(min_length=1, max_length=100)


def public_debate_payload(run) -> dict:
    return public_debate(run)


def create_app(write_key: str | None = API_WRITE_KEY,
               event_database_path: Path | None = None,
               event_graph_path: Path | None = None,
               event_debate_path: Path | None = None) -> FastAPI:
    workflow_store = WorkflowStore((event_debate_path or EVENT_DEBATE_DATABASE_PATH).with_suffix(".runs.sqlite3"))

    @asynccontextmanager
    async def lifespan(_app):
        workflow_store.interrupt_unfinished()
        yield

    api = FastAPI(title="A股企业事件研究 API", version=APP_VERSION, lifespan=lifespan,
        description="按日期检索已保存的企业原文，生成事件时间线及带反方核验的研究假设。模型调用需要 X-API-Key。")
    if API_CORS_ORIGINS:
        api.add_middleware(CORSMiddleware, allow_origins=list(API_CORS_ORIGINS),
            allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

    def require_write_access(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
        if os.getenv("EVENT_DEMO_MODE") == "true":
            raise HTTPException(status_code=403, detail="历史演示只读，不调用模型；正式运行请使用常规启动命令")
        if not write_key:
            raise HTTPException(status_code=503, detail="模型调用接口尚未配置 EVENT_API_WRITE_KEY")
        if not x_api_key or not secrets.compare_digest(x_api_key, write_key):
            raise HTTPException(status_code=401, detail="X-API-Key无效")

    def source_path_and_kind() -> tuple[Path, str]:
        if event_database_path is not None:
            path, source_kind = event_database_path, "configured"
        elif EVENT_RESEARCH_DATABASE_PATH.is_file():
            path, source_kind = EVENT_RESEARCH_DATABASE_PATH, "active"
        elif ACCEPTED_EVENT_PHASE3_PATH.is_file():
            path, source_kind = ACCEPTED_EVENT_PHASE3_PATH, "accepted_phase3_sample"
        else:
            path, source_kind = ACCEPTED_EVENT_SAMPLE_PATH, "accepted_sample"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="尚无事件资料库，请先采集资料或配置事件资料库路径")
        return path, source_kind

    def open_event_repo() -> tuple[SQLiteRepository, str]:
        path, source_kind = source_path_and_kind()
        return SQLiteRepository.open_readonly(path), source_kind

    @api.get("/", tags=["system"])
    def root():
        return {"service": api.title, "version": APP_VERSION, "docs": "/docs", "health": "/health"}

    @api.get("/health", tags=["system"])
    def health():
        graph = event_graph_path or EVENT_GRAPH_DATABASE_PATH
        debate = event_debate_path or EVENT_DEBATE_DATABASE_PATH
        try:
            source, source_kind = source_path_and_kind()
        except HTTPException:
            source, source_kind = None, "unavailable"
        return {"status": "ok", "version": APP_VERSION,
                "event_source_database_exists": source is not None,
                "event_source_kind": source_kind,
                "event_graph_database_exists": graph.is_file(),
                "event_debate_database_exists": debate.is_file(),
                "model_call_key_configured": bool(write_key) and os.getenv("EVENT_DEMO_MODE") != "true",
                "demo_mode": os.getenv("EVENT_DEMO_MODE") == "true"}

    @api.get("/api/v1/event-research/subjects", tags=["event research"])
    def event_subjects():
        repo, source_kind = open_event_repo()
        try:
            coverage = repo.conn.execute(
                "SELECT MIN(published_at) AS first_date, MAX(published_at) AS last_date "
                "FROM documents WHERE duplicate_of IS NULL AND document_type IN ('announcement','news','financial')"
            ).fetchone()
            return {"subjects": [item.model_dump(mode="json") for item in repo.list_research_subjects()
                                 if item.subject_id in EVENT_SUBJECTS],
                    "available_start": coverage["first_date"], "available_end": coverage["last_date"],
                    "source_kind": source_kind}
        finally:
            repo.close()

    @api.get("/api/v1/event-research/demo", tags=["event research"])
    def demo_catalog():
        if os.getenv("EVENT_DEMO_MODE") != "true":
            return {"enabled": False, "runs": []}
        manifest = json.loads((PROJECT_ROOT / "data/event_demo.json").read_text(encoding="utf-8"))
        return {"enabled": True, "runs": [{"run_id": run["run_id"], "label": run["graph"]["subject_name"]}
                                         for run in manifest["runs"]]}

    @api.get("/api/v1/event-research/search", tags=["event research"])
    def search_event_materials(subject_id: str, start_date: date, end_date: date,
                               query: str = Query(min_length=1, max_length=120),
                               source_types: list[EventMaterialType] | None = Query(default=None),
                               limit: int = Query(20, ge=1, le=30)):
        if subject_id not in EVENT_SUBJECTS:
            raise HTTPException(status_code=422, detail="研究对象不在支持范围内")
        if start_date > end_date:
            raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
        keyword = query.strip()
        if not keyword:
            raise HTTPException(status_code=422, detail="请输入检索关键词")
        repo, source_kind = open_event_repo()
        try:
            hits = repo.search_event_chunks(keyword, subject_id, end_date,
                window_start=start_date, source_types=source_types, limit=limit)
            return {"subject_id": subject_id, "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(), "query": keyword,
                    "source_types": source_types or [], "source_kind": source_kind,
                    "count": len(hits), "items": [{
                        "chunk_id": hit.chunk.chunk_id, "document_id": hit.chunk.document_id,
                        "title": hit.chunk.title, "publisher": hit.chunk.publisher,
                        "source_url": hit.chunk.source_url,
                        "published_at": hit.chunk.published_at.isoformat(),
                        "document_type": hit.chunk.document_type,
                        "excerpt": event_excerpt(hit.chunk.content, keyword),
                    } for hit in hits]}
        finally:
            repo.close()

    @api.post("/api/v1/event-research/plan-run", tags=["event research"],
              dependencies=[Depends(require_write_access)])
    def run_event_plan(request: EventPlanRequest):
        path, source_kind = source_path_and_kind()
        try:
            result = EventResearchOrchestrator(path).run(request.question, request.subject_code,
                request.as_of_date, request.window_start)
            return {"source_kind": source_kind, "run": result.model_dump(mode="json")}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.post("/api/v1/event-research/runs", status_code=202,
              tags=["event research"], dependencies=[Depends(require_write_access)])
    def start_research(request: EventPlanRequest, background: BackgroundTasks):
        if (not request.question.strip() or request.subject_code not in EVENT_SUBJECTS
                or request.as_of_date > date.today() or request.window_start > request.as_of_date
                or (request.as_of_date - request.window_start).days > 30):
            raise HTTPException(status_code=422, detail="研究对象、问题或30天日期窗口无效")
        path, _ = source_path_and_kind()
        run = new_run(request.model_dump(mode="json"))
        workflow_store.save(run)
        background.add_task(execute_run, run, workflow_store, path,
                            event_graph_path or EVENT_GRAPH_DATABASE_PATH,
                            event_debate_path or EVENT_DEBATE_DATABASE_PATH)
        return {"run": run}

    @api.get("/api/v1/event-research/runs/{run_id}", tags=["event research"])
    def read_research(run_id: str):
        run = workflow_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="研究运行不存在")
        return {"run": run}

    @api.post("/api/v1/event-research/timeline/build", tags=["event research"],
              dependencies=[Depends(require_write_access)])
    def build_event_timeline(request: EventPlanRequest):
        path, source_kind = source_path_and_kind()
        try:
            planning = EventResearchOrchestrator(path).run(
                request.question, request.subject_code, request.as_of_date, request.window_start)
            graph = EventGraphBuilder(path, event_graph_path or EVENT_GRAPH_DATABASE_PATH).build(planning)
            return {"source_kind": source_kind, "graph": graph.model_dump(mode="json")}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/api/v1/event-research/timeline", tags=["event research"])
    def latest_event_timeline(subject_code: str, as_of_date: date | None = None):
        if subject_code not in EVENT_SUBJECTS:
            raise HTTPException(status_code=422, detail="研究对象不在支持范围内")
        try:
            store = EventGraphStore(event_graph_path or EVENT_GRAPH_DATABASE_PATH, readonly=True)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="尚无已保存的事件时间线") from None
        try:
            graph = store.latest(subject_code, as_of_date.isoformat() if as_of_date else None)
        finally:
            store.close()
        if graph is None:
            raise HTTPException(status_code=404, detail="该公司尚无已保存的事件时间线")
        return {"graph": graph.model_dump(mode="json")}

    @api.post("/api/v1/event-research/debate/build", tags=["event research"],
              dependencies=[Depends(require_write_access)])
    def build_event_debate(request: EventDebateRequest):
        source, source_kind = source_path_and_kind()
        try:
            run = EventHypothesisDebate(source,
                event_graph_path or EVENT_GRAPH_DATABASE_PATH,
                event_debate_path or EVENT_DEBATE_DATABASE_PATH).build(request.graph_run_id)
            return {"source_kind": source_kind, "debate": public_debate_payload(run)}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/api/v1/event-research/debate", tags=["event research"])
    def latest_event_debate(subject_code: str, as_of_date: date | None = None):
        if subject_code not in EVENT_SUBJECTS:
            raise HTTPException(status_code=422, detail="研究对象不在支持范围内")
        try:
            store = EventDebateStore(event_debate_path or EVENT_DEBATE_DATABASE_PATH,
                                     readonly=True)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="尚无已保存的研究报告") from None
        try:
            run = store.latest(subject_code, as_of_date.isoformat() if as_of_date else None)
        finally:
            store.close()
        if run is None:
            raise HTTPException(status_code=404, detail="该公司尚无已保存的研究报告")
        return {"debate": public_debate_payload(run)}

    @api.get("/api/v1/event-research/debate/{run_id}", tags=["event research"])
    def event_debate_by_id(run_id: str):
        try:
            store = EventDebateStore(event_debate_path or EVENT_DEBATE_DATABASE_PATH,
                                     readonly=True)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="尚无已保存的研究报告") from None
        try:
            run = store.get(run_id)
        finally:
            store.close()
        if run is None:
            raise HTTPException(status_code=404, detail="研究报告不存在")
        return {"debate": public_debate_payload(run)}

    @api.get("/api/v1/event-research/timeline/{run_id}", tags=["event research"])
    def event_timeline_by_id(run_id: str):
        try:
            store = EventGraphStore(event_graph_path or EVENT_GRAPH_DATABASE_PATH, readonly=True)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="尚无已保存的事件时间线") from None
        try:
            graph = store.get(run_id)
        finally:
            store.close()
        if graph is None:
            raise HTTPException(status_code=404, detail="事件时间线不存在")
        return {"graph": graph.model_dump(mode="json")}

    # 构建后的前端与 API 共用同一来源地址，支持一条命令启动演示。
    web = PROJECT_ROOT / "frontend" / "dist"
    if web.is_dir():
        api.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @api.get("/{web_path:path}", include_in_schema=False)
        def frontend(web_path: str):
            from fastapi.responses import FileResponse
            if web_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="接口不存在")
            return FileResponse(web / "index.html")

    return api


app = create_app()


def run():
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
