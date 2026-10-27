"""Single hypothesis-agent baseline on an identical, precomputed evidence graph.

The comparison isolates adversarial review, not planning or graph extraction.
It keeps the same scope, quote verification, model and publication gate.
"""
from app.agents.hypothesis_debate import EventHypothesisDebate
from app.data.repositories.event_debate import EventDebateStore


class SingleEventAgent(EventHypothesisDebate):
    def _search(self, repo, packet, hypotheses, graph):
        return []

    def _counter(self, packet, hypotheses, checks, graph):
        return []

    def _report_draft(self, packet, hypotheses, findings):
        return "单Agent候选草稿；未运行独立反方核验。", None

    def build(self, graph_run_id):
        run = super().build(graph_run_id)
        if run.stop_reason == "完成反方核验":
            run.stop_reason = "单Agent候选生成结束；未运行反证检索、独立反方或报告模型。"
        run.conclusion.selected_hypothesis_id = None
        store = EventDebateStore(self.debate_database)
        try:
            store.save(run)
        finally:
            store.close()
        return run
