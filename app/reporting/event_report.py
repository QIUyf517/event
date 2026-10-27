"""生成保守的公开研究视图；旧模型生成的自由文本不能作为证据。"""

from app.models.event_research import EventDebateRun
from app.agents.typed_hypothesis import ALTERNATIVE_TEXT, KIND_TEXT


def _typed_statement(run: EventDebateRun, hypothesis) -> str:
    kind = hypothesis.interpretation_kind
    if kind != "event_candidate":
        return KIND_TEXT.get(kind, KIND_TEXT["evidence_gap"])
    claims = {claim.claim_id: claim for claim in run.graph.claims}
    events = {event.event_id: event for event in run.graph.events}
    citations = {citation.citation_id: citation for citation in run.graph.citations}
    sources = {source.document_id: source for source in run.graph.sources}
    rows = []
    seen = set()
    for claim_id in hypothesis.support_claim_ids:
        claim = claims.get(claim_id)
        if claim is None or claim.event_id in seen:
            continue
        event = events.get(claim.event_id)
        citation = citations.get(claim.citation_id)
        source = sources.get(citation.document_id) if citation else None
        if event is None or source is None:
            continue
        seen.add(event.event_id)
        occurred = (event.occurred_at.isoformat() if event.date_status == "known"
                    and event.occurred_at and event.occurred_at <= run.graph.as_of_date
                    else "未确认")
        # 这些内容来自图谱字段，不是语言模型新提出的事实。
        rows.append(f"图谱候选事件「{event.title[:80]}」：发生日{occurred}；"
                    f"所引资料发布日期{source.published_at.isoformat()}。")
    return ("".join(rows[:3]) + "与研究问题的关系尚未验证。") if rows else KIND_TEXT["evidence_gap"]


def public_debate(run: EventDebateRun) -> dict:
    payload = run.model_dump(mode="json")
    payload.pop("report_draft", None)
    for source in payload["graph"]["sources"]:
        source.pop("snapshot_path", None)
    # 主张的选择仍属于模型判断；界面直接从图谱读取日期、
    # 数值和来源类型，不通过自由文本重新表述这些信息。
    typed = run.interpretation_schema == "typed_selection_v1"
    for hypothesis, original in zip(payload["hypotheses"], run.hypotheses):
        hypothesis["statement"] = (_typed_statement(run, original) if typed else
                                   "模型选择的候选证据组合，解释方向尚未验证。")
        hypothesis["missing_evidence"] = [
            "引用关系有效不等于支持关系成立，仍需核对原文。"]
        hypothesis["alternative_explanations"] = (
            [ALTERNATIVE_TEXT[kind] for kind in original.alternative_kinds] if typed else
            ["当前材料不能排除其他解释。"])
        hypothesis["assessment"] = "cannot_judge"
    labels = {"chronology": "核对发生日与报道日是否混淆。",
              "contradiction": "核对所引原文是否真的矛盾。",
              "source_dependence": "核对来源是否独立，转载不能重复计证。",
              "omission": "核对当前资料是否遗漏相关证据。",
              "scope": "核对材料口径与研究问题是否一致。",
              "alternative": "核对是否存在其他解释。",
              "unresolved": "这项模型核查尚未解决。"}
    for finding in payload["findings"]:
        finding["detail"] = labels[finding["kind"]]
    for index, check in enumerate(payload["search_checks"], 1):
        check["query"] = f"反方原文检索{index}"
    payload["conclusion"] = {
        "level": "cannot_judge", "answer_status": "partial" if run.graph.claims else "insufficient_evidence",
        "text": "已保留可定位的原文与候选证据组合；解释关系尚未独立验证，无法确认因果。",
        "selected_hypothesis_id": None,
        "limitations": ["对外文字由程序按图谱生成，模型自由文本仅保存为内部审计记录。",
                        "来源发布日期不能替代事件发生日；来源表述一致不能证明来源独立。",
                        "图谱提取与候选证据的相关性尚未独立验证。"],
    }
    payload["publication_mode"] = ("typed_evidence_pending_review" if typed else
                                   "evidence_bound_pending_review")
    return payload
