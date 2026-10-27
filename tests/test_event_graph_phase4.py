"""第四阶段验收用例，覆盖来源绑定、冲突处理和持久化。"""

import hashlib
import json
from datetime import date

from fastapi.testclient import TestClient

from app.agents.event_graph_builder import (EventGraphBuilder, _financial_segment,
                                            _locate_quote, _numbers_agree, _numeric_qualifier,
                                            _occurrence_date_supported, _period_label,
                                            _quarter_profit_check, _supplement_quarter_mismatch,
                                            _supplement_resolutions,
                                            _supplement_explicit_disclosure)
from app.agents.event_orchestrator import EventResearchOrchestrator
from app.api.main import create_app
from app.data.repositories.event_graph import EventGraphStore
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_event_research_subjects
from app.models.event_graph import GraphClaim
from app.rag.ingest import build_document


START, END = date(2026, 8, 14), date(2026, 9, 13)
FIRST = "贵州茅台于2026年8月18日发布2026年半年度报告，营业收入为100亿元。"
SECOND = "贵州茅台于2026年8月19日发布2026年半年度报告，营业收入为105亿元。"
THIRD = "另一篇报道指出贵州茅台于2026年8月18日发布2026年半年度报告，营业收入为100亿元。"


def test_document_signature_is_not_event_occurrence_date():
    assert not _occurrence_date_supported("特此公告。贵州茅台酒股份有限公司董事会2026年8月15日",
                                          date(2026, 8, 15))
    assert _occurrence_date_supported("2026年8月13日，会议在公司会议中心召开。",
                                      date(2026, 8, 13))
    assert _occurrence_date_supported(
        "2026 年 9 月 4 日，公司 2021 年科创板限制性股票激励计划预留授予部分第四个归属期的股份完成登记手续。",
        date(2026, 9, 4))
    assert not _occurrence_date_supported("截至2026年8月14日，公司收到股票出资款。",
                                          date(2026, 8, 14))
    assert not _occurrence_date_supported("公司将于2026年9月10日上市流通。",
                                          date(2026, 9, 10))
    assert not _occurrence_date_supported("本次股票上市流通日期为2026 年 9 月 10 日。",
                                          date(2026, 9, 10))
    assert _occurrence_date_supported("2026年8月28日出具了《验资报告》。",
                                      date(2026, 8, 28))
    assert _occurrence_date_supported(
        "2026年8月20日，中国平安公布截至2026年6月30日止中期业绩。",
        date(2026, 8, 20))


def test_company_dated_release_is_kept_separate_from_database_publication_date():
    original = "2026年8月20日，中国平安公布截至2026年6月30日止中期业绩。"
    chunks = {"ev-company": {"document_type": "financial", "chunk_content": original}}
    added = _supplement_explicit_disclosure([], chunks, "中国平安", 2026)
    assert len(added) == 1
    assert added[0]["occurred_at"] == "2026-08-20"
    assert added[0]["quote"] in original
    assert len(_supplement_explicit_disclosure(added, chunks, "中国平安", 2026)) == 1


def test_pdf_line_wrap_is_mapped_back_to_exact_saved_quote():
    source = "2026 年 9 月 4 日，公司预留授予部分第四个归属期的股份完成\n登记手续。"
    proposed = "2026年9月4日，公司预留授予部分第四个归属期的股份完成登记手续。"
    assert _locate_quote(source, proposed) == source
    assert _locate_quote(source, "2026年9月4日，公司已完成其他登记手续。") is None


def test_omitted_numbered_board_resolution_keeps_original_quote():
    source = "四、审议通过了《关于审议公司信息科技外包战略的议案》"
    chunks = {"ev-1": {"document_type": "announcement", "chunk_content": source}}
    added = _supplement_resolutions([], chunks)
    assert len(added) == 1
    assert added[0]["quote"] == source
    assert added[0]["target"] == "关于审议公司信息科技外包战略的议案"
    assert len(_supplement_resolutions(added, chunks)) == 1


def test_omitted_quarter_arithmetic_conflict_is_retained_for_review():
    article = ("报告期内实现净利润445.17亿元。一季报实现净利润272.4亿元。"
               "分单季度来看，贵州茅台第二季度为172.1亿元。")
    chunks = {"ev-1": {"document_type": "news", "chunk_content": article,
                       "document_content": article}}
    added = _supplement_quarter_mismatch([], chunks, "贵州茅台", 2026)
    assert len(added) == 1
    assert added[0]["value_text"] == "172.1亿元"
    assert added[0]["quote"] == article


class FakePlanningLLM:
    configured = True

    def chat(self, messages, **kwargs):
        plan = {"steps": [{"step_id": "all", "objective": "查找披露和报道",
            "tool": "search_all_materials", "query": "营业收入", "depends_on": [],
            "completion_condition": "已找到原文", "failure_fallback": "资料不足",
            "selection_reason": "需要跨来源核对", "min_results": 1, "on_failure": "continue"}],
            "stop_condition": "已取得原文"}
        return {"message": {"content": json.dumps(plan, ensure_ascii=False)},
                "usage": {"total_tokens": 100}, "model": "fake-model"}


class FakeExtractionLLM:
    configured = True

    def __init__(self, *, bad_quote=False):
        self.bad_quote = bad_quote

    def chat(self, messages, **kwargs):
        snippets = json.loads(messages[1]["content"])["snippets"]
        items = []
        for snippet in snippets:
            content = snippet["content"]
            if "105亿元" in content:
                quote, day, value = SECOND, "2026-08-19", "105亿元"
            elif "另一篇报道" in content:
                quote, day, value = THIRD, "2026-08-18", "100亿元"
            else:
                quote, day, value = FIRST, "2026-08-18", "100亿元"
            items.append({"evidence_id": snippet["evidence_id"],
                "quote": "不存在的原文引用" if self.bad_quote else quote,
                "event_type": "financial_disclosure", "title": "贵州茅台半年度报告披露",
                "target": "2026年半年度报告", "period_label": "2026年上半年",
                "occurred_at": day, "importance": "high", "attribute": "营业收入",
                "value_text": value})
        return {"message": {"content": json.dumps({"items": items}, ensure_ascii=False)},
                "usage": {"total_tokens": 240, "prompt_tokens": 150,
                          "completion_tokens": 90}, "model": "fake-model"}


def corpus(tmp_path):
    source = tmp_path / "sources.sqlite3"
    repo = SQLiteRepository(source)
    seed_event_research_subjects(repo)
    for index, (day, text, kind) in enumerate((
            (date(2026, 8, 20), FIRST, "financial"),
            (date(2026, 8, 21), SECOND, "news"),
            (date(2026, 8, 22), THIRD, "news")), 1):
        document, chunks = build_document(
            f"<article><p>{text}</p></article>".encode("utf-8"), "source.html",
            f"报道{index}", f"来源{index}", day,
            f"https://www.stcn.com/article/phase4-{index}.html", kind,
            subject_id="600519", source_id=f"publisher-{index}")
        repo.store_event_document(document, chunks)
    repo.close()
    return source


def planned(source):
    return EventResearchOrchestrator(source, FakePlanningLLM()).run(
        "核对半年报营业收入", "600519", END, START)


def test_phase4_groups_repeated_reports_keeps_both_conflicts_and_original_database(tmp_path):
    source = corpus(tmp_path)
    graph_path = tmp_path / "graph.sqlite3"
    planning = planned(source)
    assert len(planning.evidence) == 3
    original_digest = hashlib.sha256(source.read_bytes()).hexdigest()

    graph = EventGraphBuilder(source, graph_path, FakeExtractionLLM()).build(planning)
    assert graph.status == "extracted"
    assert len(graph.events) == 1
    assert len(graph.sources) == len(graph.citations) == len(graph.claims) == 3
    event = graph.events[0]
    assert event.date_status == "conflict" and event.occurred_at is None
    assert set(event.document_ids) == {source.document_id for source in graph.sources}
    assert {conflict.kind for conflict in graph.conflicts} == {"number", "occurred_date"}
    assert {relation.kind for relation in graph.relations} == {"agreement", "conflict"}
    assert {tuple(conflict.variants) for conflict in graph.conflicts} == {
        ("100亿元", "105亿元"), ("2026-08-18", "2026-08-19")}
    assert all(claim.numeric_status == "format_checked" for claim in graph.claims)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_digest

    second = EventGraphBuilder(source, graph_path, FakeExtractionLLM()).build(planning)
    assert second.run_id == graph.run_id
    store = EventGraphStore(graph_path, readonly=True)
    try:
        assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.conn.execute("SELECT count(*) FROM graph_runs").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM graph_events").fetchone()[0] == 1
        assert store.conn.execute("SELECT count(*) FROM graph_conflicts").fetchone()[0] == 2
        assert store.get(graph.run_id).events[0].date_status == "conflict"
    finally:
        store.close()


def test_model_quote_not_in_source_is_rejected_without_inventing_events(tmp_path):
    source = corpus(tmp_path)
    graph = EventGraphBuilder(source, tmp_path / "graph.sqlite3",
                              FakeExtractionLLM(bad_quote=True)).build(planned(source))
    assert graph.status == "insufficient_evidence"
    assert not graph.events and not graph.claims and not graph.citations
    assert any("逐字定位" in warning for warning in graph.warnings)


def test_financial_rounding_and_business_unit_do_not_create_false_conflicts():
    news = GraphClaim(claim_id="news", event_id="event", citation_id="news-citation",
                      attribute="营业收入", value_text="386.35亿元",
                      numeric_status="format_checked", text="营业收入386.35亿元")
    report = GraphClaim(claim_id="report", event_id="event", citation_id="report-citation",
                        attribute="营业收入", value_text="38,635,115",
                        unit_context="原文表头标注单位：千元",
                        numeric_status="format_checked", text="营业收入 38,635,115")
    assert _numbers_agree(news, report, {
        "news": ("money", 38635000000), "report": ("money", 38635115000)})
    assert not _numbers_agree(news, report, {
        "news": ("money", 38635000000), "report": ("money", 38000000000)})
    assert _financial_segment("平安银行2026年上半年营业收入") == "平安银行"
    assert _financial_segment("北大医疗集团营业收入28亿元") == "北大医疗集团"
    assert _financial_segment("平安健康营业收入24.84亿元") == "平安健康"
    assert _period_label("2026年1-6月") == "2026年上半年"
    assert _numeric_qualifier("1,500", "上半年救援服务申请超1,500笔") == "超过"


def test_article_quarter_profit_arithmetic_is_marked_unverified():
    article = ("报告期内实现净利润445.17亿元。"
               "一季报实现净利润272.4亿元。"
               "贵州茅台第二季度为172.1亿元。")
    check = _quarter_profit_check(article, "第二季度净利润", "贵州茅台2026Q2", "172.1亿元")
    assert check is not None
    assert "172.77" in check[0] and "口径差异尚未核实" in check[0]


def test_authenticated_api_writes_separate_graph_and_read_endpoint_is_readonly(tmp_path, monkeypatch):
    import app.api.main as api_module

    source = corpus(tmp_path)
    graph_path = tmp_path / "graph.sqlite3"
    planning = planned(source)
    monkeypatch.setattr(api_module.EventResearchOrchestrator, "run",
                        lambda self, *args: planning)
    monkeypatch.setattr(api_module, "EventGraphBuilder",
                        lambda source_path, graph_path: EventGraphBuilder(
                            source_path, graph_path, FakeExtractionLLM()))
    app = create_app(event_database_path=source, event_graph_path=graph_path,
                     write_key="test-key")
    payload = {"question": planning.question, "subject_code": "600519",
               "window_start": START.isoformat(), "as_of_date": END.isoformat()}
    with TestClient(app) as client:
        assert client.get("/api/v1/event-research/timeline",
                          params={"subject_code": "600519"}).status_code == 404
        assert not graph_path.exists()
        assert client.post("/api/v1/event-research/timeline/build", json=payload).status_code == 401
        assert not graph_path.exists()
        built = client.post("/api/v1/event-research/timeline/build", json=payload,
                            headers={"X-API-Key": "test-key"})
        assert built.status_code == 200, built.text
        assert built.json()["graph"]["events"][0]["date_status"] == "conflict"
        latest = client.get("/api/v1/event-research/timeline", params={"subject_code": "600519"})
        assert latest.status_code == 200
        assert latest.json()["graph"]["run_id"] == built.json()["graph"]["run_id"]
        assert client.get("/api/v1/event-research/timeline",
                           params={"subject_code": "000000"}).status_code == 422
