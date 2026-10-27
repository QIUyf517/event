"""Typed hypothesis selection: the model chooses relationships, never fact prose."""
from __future__ import annotations

from app.agents.hypothesis_debate import (CAUSAL_QUESTION, EventHypothesisDebate,
                                          _unique_texts)
from app.models.event_graph import EventGraphRun
from app.models.event_research import DebateFinding, DebateHypothesis


KINDS = {"event_candidate", "industry_factor", "market_factor", "evidence_gap"}
ALTERNATIVES = {"other_company_event", "industry_factor", "market_factor", "unknown"}
REASONS = {"date_unknown", "numeric_conflict", "source_dependence", "market_missing",
           "counter_unresolved", "other"}
KIND_TEXT = {
    "event_candidate": "候选方向：核对所引公司事件与研究问题的关系；尚不能确认因果。",
    "industry_factor": "待检验方向：行业共同变化；本次所引资料尚不能确认其作用。",
    "market_factor": "待检验方向：市场整体变化；本次所引资料尚不能确认其作用。",
    "evidence_gap": "证据缺口：本次材料不足以判断所问现象的解释。",
}
ALTERNATIVE_TEXT = {
    "other_company_event": "尚需核查同期其他公司事件。",
    "industry_factor": "尚需核查行业共同变化。",
    "market_factor": "尚需核查市场整体变化。",
    "unknown": "其他可能解释尚未排除。",
}
FINDING_TEXT = {
    "chronology": "所引事件的发生时间与所问现象的先后关系待核对。",
    "contradiction": "所引主张可能存在冲突，需逐条核对原文和统计口径。",
    "source_dependence": "所引来源的独立性尚待核实，转载不能重复计证。",
    "omission": "仍需核对本次资料库是否遗漏相关材料。",
    "scope": "所引材料与研究问题的主体或统计口径需要核对。",
    "alternative": "仍需检查其他可能解释。",
    "unresolved": "本项核查尚未解决。",
}


class TypedEventHypothesisDebate(EventHypothesisDebate):
    """Production path for new runs; old runs remain readable but unpublished."""

    def _propose(self, packet: dict, graph: EventGraphRun) -> list[DebateHypothesis]:
        raw = self._ask(
            "你是假设选择Agent。只返回JSON对象 {\"hypotheses\":[]}，最多三个。"
            "每项严格包含 interpretation_kind、support_claim_ids、counter_claim_ids、alternative_kinds；"
            "不输出事实句、日期、数字、结论或其他字段。"
            "interpretation_kind只能是event_candidate、industry_factor、market_factor、evidence_gap。"
            "alternative_kinds只能选other_company_event、industry_factor、market_factor、unknown，最多3项。"
            "主张ID只能从输入中选择；支持和反对主张不能重叠。"
            "event_candidate至少选1条确实相关的支持主张；其他类型可以没有。"
            "问题若只问事件日期，只选择event_candidate或evidence_gap。"
            "因果问题若证据不足，应保留evidence_gap或待检验的行业/市场方向；不要凑确定原因。",
            packet, 1200)
        items = raw.get("hypotheses")
        if not isinstance(items, list) or len(items) > 3:
            raise ValueError("结构化候选数量无效")
        known = {claim.claim_id for claim in graph.claims}
        result, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                    "interpretation_kind", "support_claim_ids", "counter_claim_ids", "alternative_kinds"}:
                self.protocol_warnings.append("结构化候选字段无效，已丢弃")
                continue
            kind = item["interpretation_kind"]
            if kind not in KINDS:
                self.protocol_warnings.append("结构化候选类型无效，已丢弃")
                continue
            support = [self.alias_to_claim.get(value, value) for value in
                       _unique_texts(item["support_claim_ids"], maximum=12, length=100)]
            counter = [self.alias_to_claim.get(value, value) for value in
                       _unique_texts(item["counter_claim_ids"], maximum=12, length=100)]
            alternatives = _unique_texts(item["alternative_kinds"], maximum=3, length=40)
            if (set(support + counter) - known or set(support) & set(counter)
                    or set(alternatives) - ALTERNATIVES or (kind == "event_candidate" and not support)):
                self.protocol_warnings.append("结构化候选引用或证据关系无效，已丢弃")
                continue
            if not CAUSAL_QUESTION.search(graph.question) and kind in {"industry_factor", "market_factor"}:
                self.protocol_warnings.append("纯事件问题出现市场或行业解释，已丢弃")
                continue
            if kind != "event_candidate" and support:
                # Company-event claims cannot themselves support a market factor
                # or an absence-of-evidence proposition.
                support = []
                self.protocol_warnings.append("非事件方向的支持主张缺少对应证据类型，已移除")
            key = (kind, tuple(sorted(support)), tuple(sorted(counter)))
            if key in seen:
                self.protocol_warnings.append("结构化候选重复，已丢弃")
                continue
            seen.add(key)
            result.append(DebateHypothesis(
                hypothesis_id=f"h{len(result)+1}", statement=KIND_TEXT[kind],
                interpretation_kind=kind, alternative_kinds=alternatives,
                support_claim_ids=support, counter_claim_ids=counter,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=[ALTERNATIVE_TEXT[value] for value in alternatives],
                assessment="insufficient"))
        if not result:
            # A malformed model answer never becomes an unreviewed free-text claim.
            result = [DebateHypothesis(
                hypothesis_id="h1", statement=KIND_TEXT["evidence_gap"],
                interpretation_kind="evidence_gap", assessment="cannot_judge",
                missing_evidence=["模型未产生有效的结构化候选，需要人工核查。"])]
            self.protocol_warnings.append("结构化候选均无效，已保守降级")
        if CAUSAL_QUESTION.search(graph.question) and len(result) == 1:
            result.append(DebateHypothesis(
                hypothesis_id="h2", statement=KIND_TEXT["market_factor"],
                interpretation_kind="market_factor", assessment="cannot_judge",
                missing_evidence=["仍需核查同期市场因素。"],
                alternative_kinds=["unknown"],
                alternative_explanations=[ALTERNATIVE_TEXT["unknown"]]))
        return result

    def _counter(self, packet, hypotheses, checks, graph):
        raw = self._ask(
            "你是反方核验Agent。只返回JSON对象 {\"findings\":[]}，最多五项。"
            "每项严格包含 hypothesis_id、kind、claim_ids、hit_chunk_ids；不输出自由文本、日期或数字。"
            "kind只能是chronology、contradiction、source_dependence、omission、scope、alternative、unresolved。"
            "claim_ids和hit_chunk_ids只能从输入选择；没有明确依据时选unresolved，不能补造反证。",
            {"evidence": packet, "hypotheses": self._short_hypotheses(hypotheses),
             "search_checks": [check.model_dump(mode="json") for check in checks]}, 1300)
        items = raw.get("findings")
        if not isinstance(items, list) or len(items) > 5:
            raise ValueError("结构化反方数量无效")
        known_h = {h.hypothesis_id for h in hypotheses}
        known_c = {c.claim_id for c in graph.claims}
        known_hits = {hit.chunk_id for check in checks for hit in check.hits}
        findings = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"hypothesis_id", "kind", "claim_ids", "hit_chunk_ids"}:
                self.protocol_warnings.append("结构化反方字段无效，已丢弃")
                continue
            kind = item["kind"]
            if item["hypothesis_id"] not in known_h or kind not in FINDING_TEXT:
                self.protocol_warnings.append("结构化反方目标或类型无效，已丢弃")
                continue
            claims = [self.alias_to_claim.get(value, value) for value in
                      _unique_texts(item["claim_ids"], maximum=12, length=100)]
            hits = _unique_texts(item["hit_chunk_ids"], maximum=12, length=100)
            if set(claims) - known_c or set(hits) - known_hits:
                self.protocol_warnings.append("结构化反方引用无效，已丢弃")
                continue
            # A contradiction label needs a graph conflict, not merely two numbers.
            if kind == "contradiction" and not any(
                    set(claims) & set(conflict.claim_ids) for conflict in graph.conflicts):
                kind = "unresolved"
                self.protocol_warnings.append("反方冲突未在图谱中定位，已降级为待核")
            if kind == "chronology" and not claims and not hits:
                kind = "unresolved"
                self.protocol_warnings.append("反方时序疑点没有证据定位，已降级为待核")
            findings.append(DebateFinding(
                hypothesis_id=item["hypothesis_id"], kind=kind,
                detail=FINDING_TEXT[kind], claim_ids=claims, hit_chunk_ids=hits))
        return findings or self._fallback_findings(hypotheses)

    def _report_draft(self, packet, hypotheses, findings):
        raw = self._ask(
            "你是报告选择Agent。只返回JSON对象且仅含 selected_hypothesis_id、reason_codes。"
            "selected_hypothesis_id只能为输入中的候选ID或null。"
            "reason_codes只能从date_unknown、numeric_conflict、source_dependence、market_missing、counter_unresolved、other中选择，最多5项。"
            "不输出事实句、日期、数字或最终自由文本；证据不足时选null。",
            {"question": packet["question"], "hypotheses": self._short_hypotheses(hypotheses),
             "findings": [f.model_dump(mode="json") for f in findings]}, 450)
        if not isinstance(raw, dict) or set(raw) != {"selected_hypothesis_id", "reason_codes"}:
            raise ValueError("结构化报告字段无效")
        selected = raw["selected_hypothesis_id"]
        if selected is not None and selected not in {h.hypothesis_id for h in hypotheses}:
            raise ValueError("结构化报告选择了不存在的候选")
        reasons = _unique_texts(raw["reason_codes"], maximum=5, length=40)
        if set(reasons) - REASONS:
            raise ValueError("结构化报告原因类型无效")
        self.report_reason_codes = reasons
        return "结构化选择已保存；对外报告由图谱和程序生成。", selected

    def build(self, graph_run_id):
        self.report_reason_codes = []
        run = super().build(graph_run_id)
        run.interpretation_schema = "typed_selection_v1"
        run.conclusion.reason_codes = self.report_reason_codes
        from app.models.event_research import EventDebateRun
        run = EventDebateRun.model_validate(run.model_dump(mode="json"))
        # Preserve the same saved object seen by read-only API endpoints.
        from app.data.repositories.event_debate import EventDebateStore
        store = EventDebateStore(self.debate_database)
        try:
            store.save(run)
        finally:
            store.close()
        return run
