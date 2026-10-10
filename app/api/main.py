"""A股ETF研究助手的HTTP API。"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.agents.llm_research import ModelDrivenResearchAgent
from app.agents.single_agent import SingleResearchAgent
from app.config import (ALGORITHM_VERSION, ALLOWED_ETFS, API_CORS_ORIGINS, API_WRITE_KEY,
                        APP_VERSION, SETTINGS_DATABASE_PATH, database_path_for_mode)
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo, seed_profile
from app.data.sync import sync_market_data
from app.evaluation.service import DEFAULT_OUTPUT
from app.models.research import UserSettings
from app.monitoring.service import compare_etfs, run_monitor_once
from app.reporting.pdf_export import render_report_pdf, report_pdf_filename
from app.tools.research_tools import ResearchTools


Mode = Literal["demo", "live"]
ResearchMethod = Literal["rules", "ai"]


class ResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    etf_code: str = "510300"
    as_of_date: date = Field(default_factory=date.today)
    mode: Mode = "demo"
    method: ResearchMethod = "rules"


class MonitorRequest(BaseModel):
    as_of_date: date = Field(default_factory=date.today)
    mode: Mode = "demo"
    sync_live: bool = False


class SyncRequest(BaseModel):
    start_date: date = Field(default_factory=lambda: date.today().replace(month=1, day=1))
    end_date: date = Field(default_factory=date.today)


def create_app(database_resolver: Callable[[str], Path] = database_path_for_mode,
               write_key: str | None = API_WRITE_KEY, settings_path: Path | None = None) -> FastAPI:
    api = FastAPI(title="A股ETF公开资料与资金信号研究助手 API", version=APP_VERSION,
        description="提供可核验的ETF信号、研究报告、存档、PDF和监控状态。写接口需要X-API-Key。")
    if API_CORS_ORIGINS:
        api.add_middleware(CORSMiddleware, allow_origins=list(API_CORS_ORIGINS),
            allow_credentials=False, allow_methods=["GET", "POST", "PUT"], allow_headers=["*"])

    def require_write_access(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
        if not write_key:
            raise HTTPException(status_code=503, detail="写接口尚未配置ETF_API_WRITE_KEY")
        if not x_api_key or not secrets.compare_digest(x_api_key, write_key):
            raise HTTPException(status_code=401, detail="X-API-Key无效")

    def open_repo(mode: str) -> SQLiteRepository:
        if mode not in {"demo", "live"}:
            raise HTTPException(status_code=422, detail="mode只能是demo或live")
        return SQLiteRepository(database_resolver(mode))

    def open_settings_repo() -> SQLiteRepository:
        return SQLiteRepository(settings_path or SETTINGS_DATABASE_PATH)

    @api.get("/", tags=["system"])
    def root():
        return {"service": api.title, "version": APP_VERSION, "docs": "/docs", "health": "/health"}

    @api.get("/health", tags=["system"])
    def health():
        databases = {}
        for mode in ("demo", "live"):
            path = database_resolver(mode)
            databases[mode] = {"path": str(path), "exists": path.is_file(),
                               "size_bytes": path.stat().st_size if path.is_file() else 0}
        return {"status": "ok", "version": APP_VERSION, "databases": databases,
                "write_api_configured": bool(write_key)}

    @api.get("/api/v1/etfs", tags=["research"])
    def etfs():
        return [{"etf_code": code, "name": name} for code, name in ALLOWED_ETFS.items()]

    @api.get("/api/v1/evaluation/latest", tags=["system"])
    def latest_evaluation():
        if not DEFAULT_OUTPUT.is_file():
            raise HTTPException(status_code=404, detail="尚未生成评估结果，请运行etf-research evaluate")
        return json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    @api.get("/api/v1/signals/{etf_code}", tags=["research"])
    def signal_history(etf_code: str, mode: Mode = "demo",
                       start: date = Query(default_factory=lambda: date.today() - timedelta(days=180)),
                       end: date = Query(default_factory=date.today), limit: int = Query(60, ge=1, le=500)):
        if etf_code not in ALLOWED_ETFS:
            raise HTTPException(status_code=404, detail="不支持的ETF代码")
        repo = open_repo(mode)
        try:
            rows = repo.signal_history(etf_code, start, end, ALGORITHM_VERSION)[-limit:]
            return {"etf_code": etf_code, "mode": mode, "count": len(rows),
                    "items": [item.model_dump(mode="json") for item in rows]}
        finally:
            repo.close()

    @api.get("/api/v1/market/{etf_code}", tags=["research"])
    def market_history(etf_code: str, mode: Mode = "demo",
                       end: date = Query(default_factory=date.today),
                       limit: int = Query(90, ge=5, le=365)):
        if etf_code not in ALLOWED_ETFS:
            raise HTTPException(status_code=404, detail="不支持的ETF代码")
        repo = open_repo(mode)
        try:
            if mode == "demo" and repo.profile(etf_code) is None:
                seed_demo(repo, end)
            rows = repo.market(etf_code, end, limit=limit)
            return {"etf_code": etf_code, "mode": mode, "count": len(rows),
                    "items": [item.model_dump(mode="json") for item in rows]}
        finally:
            repo.close()

    @api.get("/api/v1/comparison", tags=["research"])
    def comparison(mode: Mode = "demo", as_of_date: date = Query(default_factory=date.today)):
        repo = open_repo(mode)
        try:
            if mode == "demo" and any(repo.profile(code) is None for code in ALLOWED_ETFS):
                seed_demo(repo, as_of_date)
            return compare_etfs(repo, list(ALLOWED_ETFS), as_of_date, mode)
        finally:
            repo.close()

    @api.post("/api/v1/research", tags=["research"], dependencies=[Depends(require_write_access)])
    def research(request: ResearchRequest):
        if request.etf_code not in ALLOWED_ETFS:
            raise HTTPException(status_code=422, detail="不支持的ETF代码")
        repo = open_repo(request.mode)
        try:
            if request.mode == "demo" and repo.profile(request.etf_code) is None:
                seed_demo(repo, request.as_of_date)
            if request.mode == "live":
                seed_profile(repo, request.etf_code)
            tools = ResearchTools(repo, request.mode)
            agent = (ModelDrivenResearchAgent(tools) if request.method == "ai"
                     else SingleResearchAgent(tools))
            state = agent.run(request.question, request.etf_code, request.as_of_date)
            return {"task_id": state.task_id, "report": state.final_report}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            repo.close()

    @api.post("/api/v1/sync/{etf_code}", tags=["monitoring"],
              dependencies=[Depends(require_write_access)])
    def sync_etf(etf_code: str, request: SyncRequest):
        if etf_code not in ALLOWED_ETFS:
            raise HTTPException(status_code=422, detail="不支持的ETF代码")
        if request.start_date > request.end_date:
            raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
        repo = open_repo("live")
        try:
            if repo.profile(etf_code) is None:
                seed_profile(repo, etf_code)
            return sync_market_data(repo, etf_code, request.start_date,
                                    request.end_date).model_dump(mode="json")
        finally:
            repo.close()

    @api.get("/api/v1/reports", tags=["archive"])
    def reports(mode: Mode = "demo", limit: int = Query(20, ge=1, le=100),
                offset: int = Query(0, ge=0)):
        repo = open_repo(mode)
        try:
            items = repo.list_reports(limit, offset)
            return {"mode": mode, "count": len(items), "offset": offset, "items": items}
        finally:
            repo.close()

    @api.get("/api/v1/reports/{task_id}", tags=["archive"])
    def report(task_id: str, mode: Mode = "demo"):
        repo = open_repo(mode)
        try:
            item = repo.get_report(task_id)
            if not item:
                raise HTTPException(status_code=404, detail="报告不存在")
            return item
        finally:
            repo.close()

    @api.get("/api/v1/reports/{task_id}/pdf", tags=["archive"])
    def report_pdf(task_id: str, mode: Mode = "demo"):
        repo = open_repo(mode)
        try:
            item = repo.get_report(task_id)
            if not item:
                raise HTTPException(status_code=404, detail="报告不存在")
            payload = render_report_pdf(item["report"], task_id)
            filename = report_pdf_filename(item["report"])
            return Response(payload, media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        finally:
            repo.close()

    @api.get("/api/v1/monitor/runs", tags=["monitoring"])
    def monitor_runs(mode: Mode = "live", limit: int = Query(20, ge=1, le=100)):
        repo = open_repo(mode)
        try:
            return {"mode": mode, "items": [item.model_dump(mode="json") for item in repo.recent_monitor_runs(limit)]}
        finally:
            repo.close()

    @api.get("/api/v1/monitor/scheduled", tags=["monitoring"])
    def scheduled_runs(mode: Mode = "live", limit: int = Query(20, ge=1, le=100)):
        repo = open_repo(mode)
        try:
            return {"mode": mode, "items": [item.model_dump(mode="json") for item in repo.recent_scheduled_runs(limit)]}
        finally:
            repo.close()

    @api.get("/api/v1/monitor/sync", tags=["monitoring"])
    def sync_runs(mode: Mode = "live", limit: int = Query(50, ge=1, le=200)):
        repo = open_repo(mode)
        try:
            return {"mode": mode, "items": [item.model_dump(mode="json") for item in repo.recent_sync_runs(limit)]}
        finally:
            repo.close()

    @api.get("/api/v1/monitor/alerts", tags=["monitoring"])
    def alerts(mode: Mode = "live", limit: int = Query(50, ge=1, le=200)):
        repo = open_repo(mode)
        try:
            return {"mode": mode, "items": [item.model_dump(mode="json") for item in repo.recent_monitor_alerts(limit)]}
        finally:
            repo.close()

    @api.post("/api/v1/monitor/run", tags=["monitoring"], dependencies=[Depends(require_write_access)])
    def monitor(request: MonitorRequest):
        if request.mode != "live" and request.sync_live:
            raise HTTPException(status_code=422, detail="演示模式不能联网同步")
        repo = open_repo(request.mode)
        try:
            if request.mode == "demo" and any(repo.profile(code) is None for code in ALLOWED_ETFS):
                seed_demo(repo, request.as_of_date)
            result = run_monitor_once(repo, request.as_of_date, request.mode,
                                      sync_live=request.sync_live and request.mode == "live")
            return result.model_dump(mode="json")
        finally:
            repo.close()

    @api.get("/api/v1/settings", tags=["settings"])
    def settings():
        repo = open_settings_repo()
        try:
            return repo.user_settings().model_dump(mode="json")
        finally:
            repo.close()

    @api.put("/api/v1/settings", tags=["settings"], dependencies=[Depends(require_write_access)])
    def update_settings(settings: UserSettings):
        if settings.default_etf not in ALLOWED_ETFS:
            raise HTTPException(status_code=422, detail="默认ETF不受支持")
        repo = open_settings_repo()
        try:
            return repo.save_user_settings(settings).model_dump(mode="json")
        finally:
            repo.close()

    return api


app = create_app()


def run():
    import uvicorn
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
