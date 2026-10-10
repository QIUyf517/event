"""Small, inspectable comparison; automatic checks are not a semantic verdict."""

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path
from time import perf_counter

from app.agents.llm_research import ModelDrivenResearchAgent
from app.agents.single_agent import SingleResearchAgent
from app.config import PROJECT_ROOT
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.llm.client import OpenAICompatibleClient
from app.tools.research_tools import ResearchTools

CASES = PROJECT_ROOT / "evals" / "semantic_cases.json"


def grade(case, answer, evidence):
    source_dates = answer.get("source_dates") or {}
    text = answer.get("direct_answer", "")
    citations = answer.get("citations", [])
    known = {row["citation"] for row in evidence}
    return {
        "status": answer.get("answer_status") == case["expected_status"],
        "signal": answer.get("signal") == case["expected_signal"],
        "market_date": source_dates.get("market") == case["expected_market_date"],
        "document_presence": bool(evidence) == case["expected_document"],
        "citation_coverage": not case["expected_document"] or bool(citations),
        "citations_known": all(item in known for item in citations),
        "cause_caveat": (not case["requires_cause_caveat"] or
                         (any(x in text for x in ("不能", "无法", "尚不能")) and
                          any(x in text for x in ("原因", "成因")))),
    }


def model_once(client, question, baseline):
    payload = {"question": question, "calculated_results": baseline["calculated_results"],
               "source_excerpts": baseline["source_excerpts"]}
    reply = client.chat([
        {"role": "system", "content": (
            "你是单模型ETF研究对照组。只用给定证据回答。输出JSON对象："
            '{"answer_status":"answered","direct_answer":"...","signal":"neutral",'
            '"source_dates":{"market":"YYYY-MM-DD"},"citations":["D1"]}。'
            "数据不足或因果无法核实时用partial。不得自行计算指标，引用只能使用给定编号。")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], json_output=True, max_tokens=850, thinking=False)
    value = json.loads(reply["message"]["content"])
    if not isinstance(value, dict):
        raise ValueError("单模型返回无效JSON")
    return value, reply.get("usage", {})


def report_answer(report):
    return {"answer_status": report["answer_status"], "direct_answer": report["direct_answer"],
            "signal": report["calculated_results"]["signal"],
            "source_dates": report["calculated_results"]["source_dates"],
            "citations": [row["citation"] for row in report["source_excerpts"]]}


def run_case(case, repo, include_ai):
    tools = ResearchTools(repo, "demo")
    as_of = date(2026, 9, 30)
    rows = []
    start = perf_counter()
    baseline = SingleResearchAgent(tools, OpenAICompatibleClient(enabled=False)).run(
        case["question"], case["etf_code"], as_of).final_report
    rows.append({"method": "rules", "latency_ms": round((perf_counter() - start) * 1000),
                 "answer": report_answer(baseline), "checks": grade(case, report_answer(baseline),
                     baseline["source_excerpts"]), "token_usage": 0})
    if include_ai:
        client = OpenAICompatibleClient(enabled=True)
        start = perf_counter()
        try:
            single, usage = model_once(client, case["question"], baseline)
            rows.append({"method": "single_model", "latency_ms": round((perf_counter() - start) * 1000),
                         "answer": single, "checks": grade(case, single, baseline["source_excerpts"]),
                         "token_usage": usage.get("total_tokens")})
        except Exception as exc:
            rows.append({"method": "single_model", "error": f"{type(exc).__name__}: {exc}"})
        start = perf_counter()
        try:
            state = ModelDrivenResearchAgent(tools, client).run(
                case["question"], case["etf_code"], as_of)
            report = state.final_report
            usage = sum((item.get("total_tokens", 0) or 0) for trace in state.execution_trace
                        for item in ([trace.output_summary.get("usage", {})]
                                     if trace.agent in {"模型规划 Agent", "模型核验 Agent"} else
                                     trace.output_summary.get("model_usage", [])))
            rows.append({"method": "multi_agent", "latency_ms": round((perf_counter() - start) * 1000),
                         "answer": report_answer(report), "checks": grade(case, report_answer(report),
                             report["source_excerpts"]), "token_usage": usage,
                         "role_statuses": {trace.agent: trace.status for trace in state.execution_trace}})
        except Exception as exc:
            rows.append({"method": "multi_agent", "error": f"{type(exc).__name__}: {exc}"})
    return {"case": case, "methods": rows, "manual_semantic_review": "待人工核对回答与原文；自动检查不能证明语义正确"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai", action="store_true", help="调用真实模型；会产生API费用")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "output" / "semantic_comparison.json")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="etf-semantic-eval-") as directory:
        repo = SQLiteRepository(Path(directory) / "demo.sqlite3")
        try:
            seed_demo(repo, date(2026, 9, 30))
            result = {"data_mode": "synthetic_demo", "as_of_date": "2026-09-30",
                      "model_calls_enabled": args.ai,
                      "cases": [run_case(case, repo, args.ai) for case in cases]}
        finally:
            repo.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "case_count": len(cases),
                      "methods": [x["method"] for x in result["cases"][0]["methods"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
