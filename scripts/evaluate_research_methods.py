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
    required_groups = case.get("required_term_groups", [])
    forbidden_terms = case.get("forbidden_terms", [])
    checks = {
        "status": answer.get("answer_status") == case["expected_status"],
        "document_presence": bool(evidence) == case["expected_document"],
        "citation_coverage": not case["expected_document"] or bool(citations),
        "citations_known": all(item in known for item in citations),
        "required_terms": all(any(term in text for term in alternatives)
                              for alternatives in required_groups),
        "forbidden_terms": not any(term in text for term in forbidden_terms),
        "cause_caveat": (not case["requires_cause_caveat"] or
                         (any(x in text for x in ("不能", "无法", "尚不能")) and
                          any(x in text for x in ("原因", "成因")))),
    }
    if "expected_signal" in case:
        checks["signal"] = answer.get("signal") == case["expected_signal"]
    if "expected_market_date" in case:
        checks["market_date"] = source_dates.get("market") == case["expected_market_date"]
    if "expected_share_date" in case:
        checks["share_date"] = source_dates.get("shares") == case["expected_share_date"]
    checks["all_checks_passed"] = all(checks.values())
    return checks


def aggregate_results(case_results):
    methods = sorted({row["method"] for case in case_results for row in case["methods"]})
    summary = []
    for method in methods:
        rows = [row for case in case_results for row in case["methods"] if row["method"] == method]
        successful = [row for row in rows if "checks" in row]
        check_values = [value for row in successful for name, value in row["checks"].items()
                        if name != "all_checks_passed"]
        category_rows = {}
        for case_result in case_results:
            row = next((item for item in case_result["methods"] if item["method"] == method), None)
            if row and "checks" in row:
                category_rows.setdefault(case_result["case"]["category"], []).append(
                    row["checks"]["all_checks_passed"])
        summary.append({
            "method": method,
            "completed_cases": len(successful),
            "error_cases": len(rows) - len(successful),
            "fully_passed_cases": sum(row["checks"]["all_checks_passed"] for row in successful),
            "case_pass_rate": (sum(row["checks"]["all_checks_passed"] for row in successful) /
                               len(rows) if rows else 0),
            "check_pass_rate": sum(check_values) / len(check_values) if check_values else 0,
            "average_latency_ms": (round(sum(row["latency_ms"] for row in successful) /
                                           len(successful), 1) if successful else None),
            "total_tokens": sum(row.get("token_usage") or 0 for row in successful),
            "category_pass_rates": {category: sum(values) / len(values)
                                    for category, values in category_rows.items()},
        })
    return summary


def render_markdown_summary(result):
    method_names = {"rules": "规则流程", "single_model": "单模型", "multi_agent": "多 Agent"}
    lines = [
        "# 研究方法对照评测摘要",
        "",
        f"- 固定数据：合成演示快照，查询日 {result['as_of_date']}",
        f"- 题目数量：{len(result['cases'])}",
        f"- 模型调用：{'已启用' if result['model_calls_enabled'] else '未启用'}",
        "- 自动检查只能验证结构、日期、引用和预设文字边界，不能替代人工语义判断。",
        "",
        "| 方法 | 完成题数 | 完全通过 | 题目通过率 | 检查项通过率 | 平均耗时 | Token合计 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result["summary"]:
        latency = f"{row['average_latency_ms']:.1f} ms" if row["average_latency_ms"] is not None else "暂无"
        lines.append(
            f"| {method_names.get(row['method'], row['method'])} | {row['completed_cases']} | "
            f"{row['fully_passed_cases']} | {row['case_pass_rate']:.1%} | "
            f"{row['check_pass_rate']:.1%} | {latency} | {row['total_tokens']} |"
        )
    lines.extend(["", "## 失败项目", ""])
    failures = 0
    for case_result in result["cases"]:
        for row in case_result["methods"]:
            if "error" in row:
                lines.append(f"- `{case_result['case']['id']}` · {method_names.get(row['method'], row['method'])}：运行错误 `{row['error']}`")
                failures += 1
                continue
            failed = [name for name, passed in row["checks"].items()
                      if name != "all_checks_passed" and not passed]
            if failed:
                lines.append(f"- `{case_result['case']['id']}` · {method_names.get(row['method'], row['method'])}：{', '.join(failed)}")
                failures += 1
    if not failures:
        lines.append("- 自动检查未发现失败项。")
    lines.extend([
        "", "## 人工复核", "",
        "对每份多 Agent 回答逐项检查：",
        "",
        "- [ ] 数字、单位和日期与程序结果一致。",
        "- [ ] 引用编号存在，引用内容与问题相关。",
        "- [ ] 没有把份额变化直接写成资金净流入。",
        "- [ ] 没有用背景资料臆测价格或份额变化原因。",
        "- [ ] 无法回答时明确说明未知范围。",
    ])
    return "\n".join(lines) + "\n"


def model_once(client, question, baseline):
    payload = {"question": question, "calculated_results": baseline["calculated_results"],
               "source_excerpts": baseline["source_excerpts"]}
    reply = client.chat([
        {"role": "system", "content": (
            "你是单模型ETF研究对照组，只能使用输入中的程序计算结果和资料片段。"
            "输出JSON对象，字段为answer_status、direct_answer、signal、source_dates、citations。"
            "signal必须逐字复制calculated_results.signal；source_dates必须完整复制"
            "calculated_results.source_dates中的market与shares，不得补造或省略。"
            "citations只能填写source_excerpts中实际存在的citation；没有资料片段时必须是空数组。"
            "问题要求预测、买卖建议或未实现指标时使用unsupported；"
            "数字或资料不足、因果无法核实时使用partial。"
            "合成演示资料可以按原文概括，但必须明确标记为合成资料。"
            "不得自行计算指标，不得把份额变化写成资金净流入，不得用资料臆测行情成因。")},
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
    parser.add_argument("--summary-output", type=Path,
                        default=PROJECT_ROOT / "output" / "semantic_comparison_summary.md")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="etf-semantic-eval-") as directory:
        repo = SQLiteRepository(Path(directory) / "demo.sqlite3")
        try:
            seed_demo(repo, date(2026, 9, 30))
            case_results = [run_case(case, repo, args.ai) for case in cases]
            result = {"data_mode": "synthetic_demo", "as_of_date": "2026-09-30",
                      "model_calls_enabled": args.ai, "cases": case_results,
                      "summary": aggregate_results(case_results)}
        finally:
            repo.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(render_markdown_summary(result), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary_output": str(args.summary_output),
                      "case_count": len(cases),
                      "methods": [x["method"] for x in result["cases"][0]["methods"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
