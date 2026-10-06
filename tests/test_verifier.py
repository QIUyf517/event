from datetime import date

from app.agents.single_agent import SingleResearchAgent
from app.agents.verifier import verify_generated_source_quotes
from app.data.repositories.sqlite import SQLiteRepository
from app.data.seed import seed_demo
from app.tools.research_tools import ResearchTools


def test_verifier_requires_exact_text_in_the_cited_document():
    documents = [
        {"citation": "D1", "content": "本报告记录份额变化为2.8%，并注明披露日期。"},
        {"citation": "D2", "content": "其他公告讨论做市服务。"},
    ]
    accepted, warnings = verify_generated_source_quotes([
        "本报告记录份额变化为2.8% [D1]",
        "本报告记录份额变化为2.8% [D2]",
        "份额变化为9% [D1]",
        "份额增加导致价格上涨 [D1]",
        "没有引用",
    ], documents, {"D1", "D2"})
    assert accepted == ["“本报告记录份额变化为2.8%” [D1]"]
    assert len(warnings) == 4


def test_generated_text_is_only_a_source_quote_not_a_verified_market_cause(tmp_path):
    class StubLLM:
        configured = True

        def summarize(self, question, payload):
            return [
                "本文件为合成演示资料，不代表真实公告。 [D1]",
                "近期资金变化由做市商导致 [D1]",
            ]

    repo = SQLiteRepository(tmp_path / "verifier.sqlite3")
    seed_demo(repo, date(2026, 9, 28))
    state = SingleResearchAgent(ResearchTools(repo, "demo"), llm=StubLLM()).run(
        "510300当前资金信号的原因和定期报告资料", "510300", date(2026, 9, 28))
    report = state.final_report
    assert report["source_excerpts"]
    assert report["verified_facts"] == []
    assert report["reasonable_inferences"] == []
    assert report["source_quotes"] == ["“本文件为合成演示资料，不代表真实公告” [D1]"]
    assert any("无法逐字定位" in warning for warning in report["warnings"])
    assert any("尚不能确认" in item for item in report["unverified_information"])
    assert any(item.classification == "source_excerpt" for item in state.evidence)
    repo.close()
