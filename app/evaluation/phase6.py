"""Paired synthetic-scenario ablation, intentionally separate from real sources.

Only questions, synthetic records and a shared event-label vocabulary reach the
models. Gold answers, expected keys, forbidden phrases and rubrics do not.
This measures a controlled prompting ablation, not production pipeline accuracy.
"""
from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import monotonic

from app.agents.event_orchestrator import TOOL_NAMES, DEEPSEEK_FLASH_PRICES
from app.evaluation.event_research import load_event_cases
from app.llm.client import OpenAICompatibleClient


def records_for(case):
    records = [{"id": "scenario-1", "published_at": str(case.as_of_date), "text": case.scenario}]
    # These are authored synthetic fixtures, never fetched company disclosures.
    if case.id == "future_post_cutoff_announcement":
        records = [{"id": "news-0928", "published_at": "2026-09-28", "text": "9月28日新闻提及经营情况，未提供9月经营公告。"},
                   {"id": "announcement-1002", "published_at": "2026-10-02", "text": "10月2日发布9月经营公告。"}]
    elif case.id == "future_revised_news":
        records = [{"id": "news-v1", "published_at": "2026-09-29", "text": "9月29日新闻首版：设备交付尚未确认。"},
                   {"id": "news-v2", "published_at": "2026-10-01", "text": "10月1日修订版增加公司确认设备交付的内容。"}]
    return records


def packet_for(case, vocabulary):
    records = records_for(case)
    return {"question": case.question, "subject_code": case.subject_code,
            "window_start": str(case.as_of_date - timedelta(days=30)), "as_of_date": str(case.as_of_date),
            "dataset": "synthetic_scenario_not_real_company_facts",
            "records": [r for r in records if r["published_at"] <= str(case.as_of_date)],
            "excluded_record_dates": [r["published_at"] for r in records if r["published_at"] > str(case.as_of_date)],
            "event_label_vocabulary": vocabulary, "available_tools": sorted(TOOL_NAMES)}


def expected_tools(case):
    # Rubric is never sent to a model. It is a permissive relevance check, not
    # proof of successful tool execution (this harness does not execute tools).
    if case.category == "公告解读":
        return {"search_announcements", "search_financial_materials", "search_all_materials"}
    if case.category == "候选原因":
        return {"get_company_market_data"}
    return {"search_announcements", "search_news", "search_all_materials", "search_financial_materials"}


def score(case, answer: dict, packet: dict):
    records = {r["id"]: r for r in packet["records"]}
    assertions = answer.get("assertions", [])
    citations = [c for assertion in assertions for c in assertion.get("citations", [])]
    def valid(c):
        return (isinstance(c, dict) and c.get("record_id") in records and bool(c.get("quote"))
                and c["quote"] in records[c["record_id"]]["text"])
    correct = sum(valid(c) for c in citations)
    supported = sum(bool(a.get("citations")) and all(valid(c) for c in a["citations"]) for a in assertions)
    keys = {event.get("key") for event in answer.get("events", [])}
    expected = {event.event_key for event in case.expected_events}
    text = json.dumps(answer, ensure_ascii=False)
    forbidden = [term for term in case.forbidden_conclusions if term in text]
    selected = set(answer.get("selected_tools", []))
    leak_refs = sum(c.get("record_id") not in records for c in citations if isinstance(c, dict))
    return {"tool_selection_relevance": float(bool(selected & expected_tools(case)) and selected <= TOOL_NAMES),
            "citation_exact_matches": correct, "citation_count": len(citations),
            "citation_exact_match_rate": correct / len(citations) if citations else None,
            "event_coverage": len(keys & expected) / len(expected) if expected else None,
            "unsupported_assertion_count": len(assertions) - supported, "assertion_count": len(assertions),
            "unsupported_assertion_rate": (len(assertions) - supported) / len(assertions) if assertions else None,
            "invalid_or_future_reference_count": leak_refs,
            "forbidden_phrase_hits": forbidden,
            "answer_status_matches": answer.get("answer_status") in case.expected_statuses,
            "required_points_coverage": sum(any(term in text for term in group) for group in case.required_term_groups) / max(1, len(case.required_term_groups)),
            "human_semantic_review": "pending"}


SYSTEM = '''你进行人工合成场景研究，不得把它写成真实公司事实。只输出JSON：
{"selected_tools":[工具名],"events":[{"key":"共享词表中最匹配的标签","summary":"事件及日期性质"}],
"assertions":[{"text":"能确认的事实","citations":[{"record_id":"输入记录ID","quote":"逐字引用"}]}],
"hypotheses":[{"statement":"候选解释","support_record_ids":[],"counter_record_ids":[],"missing_evidence":[],"alternatives":[]}],
"answer_status":"answered或partial或insufficient_evidence","conclusion":"含限制的回答"}。
只使用给定records；逐字引用，不得编造数字或日期。无证据不补原因。计划不等于实施，报道日不等于发生日，转载不等于独立证据。
因果题提出竞争性解释，明确无法确认因果。截止日之后内容不可用于判断，可说明排除日期。最多3个事件、3条事实、3个假设。'''


class Experiment:
    def __init__(self, budget: float, llm=None):
        self.llm = llm or OpenAICompatibleClient(enabled=True)
        self.budget = budget
        self.cost = 0.0
        self.tokens = 0
        self.calls = 0
        self.transcript = []

    def ask(self, system, payload, max_output=1600):
        import os
        if "deepseek.com" in self.llm.base_url and self.llm.model in {"deepseek-flash", "deepseek-v4-flash"}:
            rates = DEEPSEEK_FLASH_PRICES
        else:
            rates = (float(os.environ["EVENT_LLM_INPUT_USD_PER_1M"]), float(os.environ["EVENT_LLM_OUTPUT_USD_PER_1M"]))
        if min(rates) < 0:
            raise ValueError("单价不能为负")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        bound = sum(len(m["content"].encode()) for m in messages) + 1000
        if self.cost + (bound * rates[0] + max_output * rates[1]) / 1e6 > self.budget:
            raise RuntimeError("全局实验费用预算已达到，停止后续调用")
        self.calls += 1
        reply = self.llm.chat(messages, json_output=True, max_tokens=max_output, thinking=False)
        usage = reply.get("usage", {})
        total = usage.get("total_tokens")
        if not isinstance(total, int) or total < 0:
            raise RuntimeError("无法计量本次用量，停止实验")
        self.tokens += total
        self.cost += (usage.get("prompt_tokens", total) * rates[0] + usage.get("completion_tokens", total) * rates[1]) / 1e6
        self.transcript.append({"messages": messages, "reply": reply})
        if self.cost > self.budget:
            raise RuntimeError("实际费用超过预算，停止实验")
        return json.loads(reply["message"]["content"])

    def run(self, method, packet):
        if method == "rules":
            return {"selected_tools": ["search_all_materials"], "events": [], "assertions": [], "hypotheses": [],
                    "answer_status": "insufficient_evidence", "conclusion": "规则基线仅提供资料；不能进行开放式事件研究。"}
        answer = self.ask(SYSTEM, packet)
        if method == "multi":
            review = self.ask("你是独立反方核验角色。只返回JSON {issues:[], missing:[]}。逐项核对候选答案与合成原文的日期、条件、来源独立性、竞争解释及引用；不要引入外部事实。",
                              {"input": packet, "draft": answer}, 900)
            answer = self.ask(SYSTEM + "\n根据反方审查修正答案；审查意见也可能错误，仍以输入原文为准。",
                              {"input": packet, "draft": answer, "review": review})
        return answer


def run_experiment(output: Path, repeats=2, methods=("rules", "single", "multi"), budget=.5):
    if output.exists():
        raise ValueError("输出目录已存在，请使用新目录以保留既有结果")
    output.mkdir(parents=True)
    cases = load_event_cases()
    vocabulary = sorted({event.event_key for case in cases for event in case.expected_events})
    experiment = Experiment(budget)
    rows = []
    halted = False
    for repeat in range(repeats):
        for case in cases:
            packet = packet_for(case, vocabulary)
            # Rotate paid method order to reduce fixed order effects.
            order = list(methods) if repeat % 2 == 0 else list(reversed(methods))
            for method in order:
                start = monotonic()
                before_cost, before_tokens = experiment.cost, experiment.tokens
                experiment.transcript = []
                row = {"case_id": case.id, "repeat": repeat + 1, "method": method,
                       "question": case.question, "input": packet, "review_status": "pending"}
                try:
                    answer = experiment.run(method, packet)
                    row.update(answer=answer, metrics=score(case, answer, packet), status="completed")
                except Exception as exc:
                    row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                    if isinstance(exc, RuntimeError):
                        halted = True
                row.update(elapsed_seconds=round(monotonic() - start, 3),
                           total_tokens=experiment.tokens - before_tokens,
                           estimated_cost_usd=round(experiment.cost - before_cost, 8), transcript=experiment.transcript)
                filename = f"{case.id}-{method}-{repeat+1}.json"
                encoded = json.dumps(row, ensure_ascii=False, indent=2).encode()
                (output / filename).write_bytes(encoded)
                rows.append({**{k: v for k, v in row.items() if k not in {"input", "answer", "transcript"}},
                             "file": filename, "sha256": sha256(encoded).hexdigest()})
                print(f"{case.id} {method} repeat={repeat+1} {row['status']}", flush=True)
                if halted:
                    break
            if halted:
                break
        if halted:
            break
    summaries = {}
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        successful = [r for r in selected if r["status"] == "completed"]
        def avg(name):
            values = [r["metrics"][name] for r in successful if r["metrics"][name] is not None]
            return sum(values) / len(values) if values else None
        summaries[method] = {"runs": len(selected), "completed": len(successful),
            **{name: avg(name) for name in ["tool_selection_relevance", "citation_exact_match_rate", "event_coverage", "unsupported_assertion_rate", "required_points_coverage"]},
            "invalid_or_future_reference_count": sum(r["metrics"]["invalid_or_future_reference_count"] for r in successful),
            "average_seconds": sum(r["elapsed_seconds"] for r in selected) / max(1, len(selected)),
            "estimated_cost_usd": sum(r["estimated_cost_usd"] for r in selected)}
    paired = paired_summary(rows)
    summary = {"dataset": "20_synthetic_scenarios_prompt_ablation", "case_count": len(cases), "repeats": repeats,
               "paired_single_multi": paired,
               "cases_sha256": sha256(Path("evals/event_research_cases.json").read_bytes()).hexdigest(),
               "system_prompt_sha256": sha256(SYSTEM.encode()).hexdigest(),
               "model": experiment.llm.model, "budget_usd": budget, "estimated_cost_usd": experiment.cost,
               "halted": halted, "methods": summaries, "rows": rows, "human_review_pending": len(rows),
               "production_pipeline_comparison": False,
               "limitations": ["输入是人工合成场景摘要，不是真实原文快照；不能据此报告生产研究准确率。",
                               "multi为同一模型的起草、反方、修订提示词消融，不等于生产多Agent全流程。",
                               "工具指标仅为所选工具相关性；不衡量实际工具执行。",
                               "引文逐字匹配与无引用断言均为程序代理指标，不证明语义或因果正确。",
                               "事件覆盖依赖共享标签词表；没有期望事件的题不计入覆盖率均值。",
                               "禁止短语匹配不能区分否定引用；未来引用计数不覆盖所有文字形式的泄漏。",
                               "所有最终答案须人工审核，自动结果不签发验收。"]}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    review = [{"case_id": r["case_id"], "method": r["method"], "repeat": r["repeat"], "file": r["file"],
               "sha256": r["sha256"], "reviewer": None, "status": "pending", "citation_fidelity": None,
               "date_and_action_status": None, "unsupported_assertions": None, "notes": ""} for r in rows]
    (output / "human_review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def paired_summary(rows):
    keyed = {(r["case_id"], r["repeat"], r["method"]): r for r in rows if r["status"] == "completed"}
    pairs = [(case_id, repeat) for case_id, repeat, method in keyed
             if method == "single" and (case_id, repeat, "multi") in keyed]
    result = {"pairs": len(pairs), "methods": {}}
    for method in ["single", "multi"]:
        selected = [keyed[(case_id, repeat, method)] for case_id, repeat in pairs]
        metrics = {}
        for name in ["tool_selection_relevance", "citation_exact_match_rate", "event_coverage", "unsupported_assertion_rate", "required_points_coverage"]:
            values = [row["metrics"][name] for row in selected if row["metrics"][name] is not None]
            metrics[name] = sum(values) / len(values) if values else None
        result["methods"][method] = {**metrics,
            "average_seconds": sum(row["elapsed_seconds"] for row in selected) / max(1, len(selected)),
            "estimated_cost_usd": sum(row["estimated_cost_usd"] for row in selected)}
    return result
