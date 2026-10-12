"""Bounded, model-planned evidence collection for company event research.

This stage returns a plan and source excerpts. It does not publish event
timelines, causal explanations or a final research judgement.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date, datetime, time, timezone
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, Field

from app.data.repositories.sqlite import SQLiteRepository
from app.llm.client import OpenAICompatibleClient
from app.models.event_research import (EventEvidence, EventResearchPlan,
                                       EventResearchPlanStep, EventResearchTrace)
from app.tools.research_tools import EVENT_SUBJECTS, EventResearchTools


TOOL_NAMES = frozenset({"search_announcements", "search_news",
                        "search_financial_materials", "search_all_materials",
                        "get_company_market_data"})
MAX_STEPS = 6
MAX_ROUNDS = 6
MAX_RESULTS_PER_STEP = 5
MAX_TOTAL_TOKENS = 3000
MAX_SECONDS = 30
MAX_MODEL_OUTPUT_TOKENS = 1500
MAX_ESTIMATED_USD = 0.01
DEEPSEEK_FLASH_PRICES = (0.30, 1.20)  # Peak USD per 1M tokens, checked 2026-10-12.


class EventPlanningRun(BaseModel):
    question: str
    subject_code: str
    as_of_date: date
    window_start: date
    plan: EventResearchPlan
    evidence: list[EventEvidence] = Field(default_factory=list)
    execution_trace: list[EventResearchTrace] = Field(default_factory=list)
    status: Literal["evidence_collected", "partial", "insufficient_evidence"]
    stop_reason: str
    model: str
    total_tokens: int
    estimated_cost_usd: float
    estimated_cost_cap_usd: float
    planning_rounds: int = 1
    limitations: list[str] = Field(default_factory=lambda: [
        "本阶段只完成模型规划和资料取证，尚未生成事件时间线、竞争性假设或研究结论。"])


class EventResearchOrchestrator:
    """The LLM chooses a typed DAG; Python enforces scope and runs read-only tools."""

    def __init__(self, database_path: Path, llm: OpenAICompatibleClient | None = None):
        self.database_path = Path(database_path)
        self.llm = llm if llm is not None else OpenAICompatibleClient()

    def _plan(self, question: str, subject_code: str, subject_name: str,
              window_start: date, as_of_date: date) -> tuple[EventResearchPlan, dict]:
        available = ", ".join(sorted(TOOL_NAMES))
        messages = [
            {"role": "system", "content": (
                "你是企业事件研究的规划者。只返回 JSON 对象，包含 steps 和 stop_condition。"
                "每个 step 必须有 step_id、objective、tool、query、depends_on、"
                "completion_condition、failure_fallback、selection_reason、min_results、on_failure。"
                f"tool 只能选 {available}；on_failure 只能是 continue 或 stop。"
                "工具只查询本地已保存资料；get_company_market_data 读取已保存的公司历史日线，query 只写研究目标。"
                "公告、新闻、财务和行情可并行；只有确实需要前置证据时才添加依赖。"
                "不同问题应选择不同工具路径；不要生成事实、事件结论、因果判断或投资建议。"
                "通常选择二至四步，最多六步；每个文字字段尽量不超过二十字。"
                "依赖必须无环。不得修改主体和日期，也不能请求联网、写入或其他工具。")},
            {"role": "user", "content": (
                f"主体：{subject_name}（{subject_code}）\n"
                f"资料日期：{window_start} 至 {as_of_date}\n问题：{question}")},
        ]
        cap = float(os.getenv("EVENT_LLM_MAX_USD_PER_RUN", str(MAX_ESTIMATED_USD)))
        if cap <= 0:
            raise ValueError("模型费用预算必须大于零")
        model_id = str(getattr(self.llm, "model", "fake-model"))
        base_url = str(getattr(self.llm, "base_url", ""))
        if "deepseek.com" in base_url and model_id in {"deepseek-flash", "deepseek-v4-flash"}:
            input_rate, output_rate = DEEPSEEK_FLASH_PRICES
        elif model_id == "fake-model":
            input_rate = output_rate = 0.0
        else:
            try:
                input_rate = float(os.environ["EVENT_LLM_INPUT_USD_PER_1M"])
                output_rate = float(os.environ["EVENT_LLM_OUTPUT_USD_PER_1M"])
            except (KeyError, ValueError) as exc:
                raise RuntimeError("未知模型必须配置输入和输出 Token 单价，才能执行费用预算") from exc
        if input_rate < 0 or output_rate < 0:
            raise ValueError("模型单价不能为负")
        # UTF-8 byte count plus protocol allowance is conservative for this text-only request.
        input_bound = sum(len(item["content"].encode("utf-8")) for item in messages) + 1000
        preflight_cost = (input_bound * input_rate + MAX_MODEL_OUTPUT_TOKENS * output_rate) / 1_000_000
        if preflight_cost > cap:
            raise ValueError("模型调用的预估费用超过本次预算")
        reply = self.llm.chat(messages, json_output=True, max_tokens=MAX_MODEL_OUTPUT_TOKENS, thinking=False)
        usage = reply.get("usage") or {}
        total_tokens = usage.get("total_tokens")
        if not isinstance(total_tokens, int) or total_tokens < 0:
            raise ValueError("模型未返回可核算的 Token 用量")
        if total_tokens > MAX_TOTAL_TOKENS:
            raise ValueError("模型规划超出 Token 预算")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
        else:
            cost = total_tokens * max(input_rate, output_rate) / 1_000_000
        if cost > cap:
            raise ValueError("模型规划超出费用预算")
        content = reply.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("模型规划缺少 JSON 内容")
        raw = json.loads(content)
        if not isinstance(raw, dict) or set(raw) - {"steps", "stop_condition", "subject_code", "as_of_date", "window_start"}:
            raise ValueError("模型规划包含未授权字段")
        for key, expected in (("subject_code", subject_code), ("as_of_date", str(as_of_date)),
                              ("window_start", str(window_start))):
            if key in raw and raw[key] != expected:
                raise ValueError("模型规划试图修改研究主体或日期")
        steps = raw.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError("研究计划步骤数不在允许范围内")
        allowed_fields = {"step_id", "objective", "tool", "query", "depends_on",
                          "completion_condition", "failure_fallback", "selection_reason",
                          "min_results", "on_failure"}
        validated = []
        for item in steps:
            if not isinstance(item, dict) or set(item) != allowed_fields:
                raise ValueError("研究计划节点字段不完整或包含未授权参数")
            if item["tool"] not in TOOL_NAMES:
                raise ValueError("模型请求了未授权工具")
            if not isinstance(item["query"], str) or not 1 <= len(item["query"].strip()) <= 120:
                raise ValueError("检索关键词长度无效")
            if not isinstance(item["selection_reason"], str) or not item["selection_reason"].strip():
                raise ValueError("模型必须说明工具选择理由")
            validated.append(EventResearchPlanStep.model_validate(item))
        if not isinstance(raw.get("stop_condition"), str) or not raw["stop_condition"].strip():
            raise ValueError("研究计划缺少停止条件")
        plan = EventResearchPlan(subject_code=subject_code, as_of_date=as_of_date,
            window_start=window_start, steps=validated, stop_condition=raw["stop_condition"])
        return plan, {"model": str(reply.get("model") or "unknown"), "total_tokens": total_tokens,
                      "estimated_cost_usd": round(cost, 8), "estimated_cost_cap_usd": cap}

    def _execute_step(self, step: EventResearchPlanStep, subject_code: str,
                      window_start: date, as_of_date: date, deadline: float):
        repo = SQLiteRepository.open_readonly(self.database_path)
        try:
            repo.conn.set_progress_handler(lambda: int(monotonic() > deadline), 1000)
            if monotonic() > deadline:
                raise TimeoutError("研究执行超时")
            tool = getattr(EventResearchTools(repo), step.tool)
            return tool(step.query, subject_code, window_start, as_of_date,
                        limit=MAX_RESULTS_PER_STEP)
        finally:
            repo.close()

    def run(self, question: str, subject_code: str, as_of_date: date,
            window_start: date) -> EventPlanningRun:
        question = question.strip()
        if not 1 <= len(question) <= 500:
            raise ValueError("研究问题长度必须为1至500字")
        if subject_code not in EVENT_SUBJECTS:
            raise ValueError("研究对象不在支持范围内")
        if as_of_date > date.today() or window_start > as_of_date or (as_of_date - window_start).days > 30:
            raise ValueError("研究日期必须在过去且窗口不超过30天")
        if not self.database_path.is_file():
            raise FileNotFoundError("事件资料库不存在")
        if not self.llm.configured:
            raise RuntimeError("开放式事件研究需要已配置的大模型；基础资料检索仍可使用")
        repo = SQLiteRepository.open_readonly(self.database_path)
        try:
            subject = EventResearchTools(repo).get_subject(subject_code)
            if subject is None or not subject.enabled:
                raise ValueError("事件资料库中没有该研究对象")
        finally:
            repo.close()

        plan, usage = self._plan(question, subject_code, subject.name, window_start, as_of_date)
        deadline = monotonic() + MAX_SECONDS
        pending = {step.step_id: step for step in plan.steps}
        completed: dict[str, str] = {}
        traces: list[EventResearchTrace] = []
        evidence: dict[str, EventEvidence] = {}
        stopped = False
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            rounds = 0
            while pending:
                rounds += 1
                if rounds > MAX_ROUNDS or monotonic() > deadline:
                    raise TimeoutError("研究执行超出轮次或时间预算")
                ready = [step for step in pending.values()
                         if all(dependency in completed for dependency in step.depends_on)]
                if not ready:
                    raise ValueError("研究计划存在无法执行的依赖")
                futures = {}
                for step in ready:
                    del pending[step.step_id]
                    started = datetime.now(timezone.utc)
                    if stopped or any(completed[dependency] != "success" for dependency in step.depends_on):
                        traces.append(EventResearchTrace(step_id=step.step_id, agent="资料检索",
                            status="skipped", started_at=started, finished_at=started,
                            stop_reason="前置任务失败或计划已停止"))
                        completed[step.step_id] = "skipped"
                    else:
                        futures[step.step_id] = (step, started, executor.submit(
                            self._execute_step, step, subject_code, window_start, as_of_date, deadline))
                for step_id, (step, started, future) in futures.items():
                    try:
                        hits = future.result(timeout=max(0.01, deadline - monotonic()))
                        new_evidence = {}
                        for hit in hits:
                            if step.tool == "get_company_market_data":
                                day = date.fromisoformat(hit["trade_date"])
                                if not window_start <= day <= as_of_date or hit["subject_id"] != subject_code:
                                    raise ValueError("行情工具返回了研究范围之外的数据")
                                evidence_id = f"market-{subject_code}-{day}"
                                new_evidence[evidence_id] = EventEvidence(
                                    evidence_id=evidence_id, source_id=hit["source"], source_type="market",
                                    title=f"{subject.name} {day} 日线", publisher=hit["source"],
                                    source_url=hit["source_url"], published_at=datetime.combine(day, time.min),
                                    excerpt=f"未复权收盘价 {hit['close']} 元；成交量 {hit['volume']} 手；成交额 {hit['turnover']} 元。")
                            else:
                                chunk = hit.chunk
                                if not window_start <= chunk.published_at <= as_of_date:
                                    raise ValueError("检索工具返回了研究日期之外的资料")
                                evidence_id = f"ev-{chunk.chunk_id}"
                                new_evidence[evidence_id] = EventEvidence(evidence_id=evidence_id,
                                    source_id=chunk.source_id, source_type=chunk.document_type,
                                    title=chunk.title, publisher=chunk.publisher,
                                    source_url=chunk.source_url,
                                    published_at=datetime.combine(chunk.published_at, time.min),
                                    excerpt=chunk.content[:500])
                        evidence.update(new_evidence)
                        new_refs = list(new_evidence)
                        success = len(hits) >= step.min_results
                        error = None if success else f"仅取得{len(hits)}条片段，未满足最少{step.min_results}条"
                    except (FutureTimeout, TimeoutError) as exc:
                        success, new_refs, error = False, [], f"检索超时：{exc}"
                    except Exception as exc:
                        success, new_refs, error = False, [], f"检索失败：{exc}"
                    completed[step_id] = "success" if success else "failed"
                    traces.append(EventResearchTrace(step_id=step_id, agent="资料检索",
                        status=completed[step_id], started_at=started,
                        finished_at=datetime.now(timezone.utc), tool_calls=[step.tool],
                        evidence_ids=new_refs, stop_reason="达到节点完成条件" if success else step.failure_fallback,
                        error=error))
                    if not success and step.on_failure == "stop":
                        stopped = True
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        failures = any(trace.status != "success" for trace in traces)
        status = ("partial" if evidence and failures else "evidence_collected" if evidence
                  else "insufficient_evidence")
        reason = ("有检索节点失败，保留已取得的原文片段" if failures else
                  "计划节点已执行完毕" if evidence else "本次检索没有取得原文片段")
        return EventPlanningRun(question=question, subject_code=subject_code,
            as_of_date=as_of_date, window_start=window_start, plan=plan,
            evidence=list(evidence.values()), execution_trace=traces,
            status=status, stop_reason=reason, **usage)
