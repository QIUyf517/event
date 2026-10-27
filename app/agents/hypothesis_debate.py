"""在限定范围内生成假设、检索反证，并用确定性规则把关证据。"""

from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

import requests

from app.agents.event_orchestrator import DEEPSEEK_FLASH_PRICES
from app.agents.event_graph_builder import (DATE_RE, _number, _numbers_agree,
                                            _occurrence_date_supported)
from app.data.repositories.event_debate import EventDebateStore
from app.data.repositories.event_graph import EventGraphStore
from app.data.repositories.sqlite import SQLiteRepository
from app.llm.client import OpenAICompatibleClient
from app.models.event_graph import EventGraphRun
from app.models.event_research import (DebateConclusion, DebateFinding, DebateHypothesis,
                                       DebateSearchCheck, DebateSearchHit, EventDebateRun)


MAX_CALLS = 4
MAX_TOTAL_TOKENS = 36000
MAX_SECONDS = 180
DEFAULT_COST_CAP_USD = 0.08
MAX_SEARCH_HITS = 4
FORBIDDEN_CERTAINTY = re.compile(r"(证明.{0,8}(导致|造成)|确定.{0,8}(导致|造成)|必然.{0,8}(上涨|下跌)|唯一原因|保证收益)")
MARKET_QUESTION = re.compile(r"股价|成交|涨跌|价格|行情|市场反应")
CAUSAL_QUESTION = re.compile(r"原因|为什么|导致|影响|关联|有关|驱动|解释|反证|假设|关系")
FINDING_KINDS = {"chronology", "contradiction", "source_dependence", "omission",
                 "scope", "alternative", "unresolved"}


def _plain_text(value: str) -> str:
    value = re.sub(r"(?<![A-Za-z0-9])event-[a-f0-9]{12,}(?![A-Za-z0-9])",
                   "对应事件", value)
    value = re.sub(r"(?<![A-Za-z0-9])\d{6}-[a-f0-9]{12,}(?![A-Za-z0-9])",
                   "所引公告", value)
    alias = r"c[A-Za-z]*\d+"
    value = re.sub(rf"(?<![A-Za-z0-9]){alias}\s*[-－]\s*{alias}(?![A-Za-z0-9])",
                   "所引多条原文", value)
    value = re.sub(rf"(?<![A-Za-z0-9]){alias}\s*(?:与|和|、|及)\s*{alias}(?![A-Za-z0-9])",
                   "所引两条原文", value)
    value = re.sub(rf"(?<![A-Za-z0-9]){alias}(?![A-Za-z0-9])", "所引原文", value)

    numerals = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五"}

    def public_hypothesis(match: re.Match[str]) -> str:
        number = match.group(1)
        return f"候选解释{numerals.get(number, number)}"

    value = re.sub(r"(?<![A-Za-z0-9])h(\d+)(?![A-Za-z0-9])",
                   public_hypothesis, value)
    value = value.replace("published_at", "资料库发布日期")
    value = value.replace("资料库资料库发布日期", "资料库发布日期")
    value = re.sub(
        r"所引(?:两条|多条)?原文(?:\s*[-–—、]\s*所引(?:两条|多条)?原文)+",
        "所引多条原文", value)
    return value


def _unique_texts(value, *, maximum: int, length: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("模型输出的文本列表类型无效")
    result = []
    for item in value:
        if not isinstance(item, str) or len(item) > length:
            raise ValueError("模型输出的文本列表包含无效内容")
        item = item.strip()
        if not item:
            continue
        if item not in result:
            result.append(item)
    if len(result) > maximum:
        raise ValueError("模型输出超过列表上限")
    return result


class EventHypothesisDebate:
    def __init__(self, source_database: str | Path, graph_database: str | Path,
                 debate_database: str | Path, llm: OpenAICompatibleClient | None = None):
        self.source_database = Path(source_database)
        self.graph_database = Path(graph_database)
        self.debate_database = Path(debate_database)
        if len({p.resolve() for p in (self.source_database, self.graph_database,
                                      self.debate_database)}) != 3:
            raise ValueError("资料库、图谱库和研究报告库必须分开")
        self.llm = llm if llm is not None else OpenAICompatibleClient()
        self.tokens = 0
        self.cost = 0.0
        self.calls = 0
        self.started = 0.0
        self.protocol_warnings: list[str] = []
        self.alias_to_claim: dict[str, str] = {}
        self.claim_to_alias: dict[str, str] = {}

    def _rates(self) -> tuple[float, float]:
        model_id = str(getattr(self.llm, "model", "fake-model"))
        base_url = str(getattr(self.llm, "base_url", ""))
        if model_id == "fake-model":
            return (0.0, 0.0)
        if "deepseek.com" in base_url and model_id in {"deepseek-flash", "deepseek-v4-flash"}:
            return DEEPSEEK_FLASH_PRICES
        try:
            rates = (float(os.environ["EVENT_LLM_INPUT_USD_PER_1M"]),
                     float(os.environ["EVENT_LLM_OUTPUT_USD_PER_1M"]))
        except (KeyError, ValueError) as exc:
            raise RuntimeError("未知模型必须配置输入和输出 Token 单价") from exc
        if min(rates) < 0:
            raise ValueError("模型单价不能为负")
        return rates

    def _ask(self, system: str, payload: dict, max_output: int) -> dict:
        if self.calls >= MAX_CALLS or monotonic() - self.started > MAX_SECONDS:
            raise RuntimeError("反方研究达到调用或时间上限")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]
        input_rate, output_rate = self._rates()
        cap = float(os.getenv("EVENT_DEBATE_MAX_USD_PER_RUN", str(DEFAULT_COST_CAP_USD)))
        if cap <= 0:
            raise ValueError("反方研究费用预算必须大于零")
        input_bound = sum(len(item["content"].encode("utf-8")) for item in messages) + 1000
        if self.cost + (input_bound * input_rate + max_output * output_rate) / 1_000_000 > cap:
            raise ValueError("反方研究调用前费用预估超过预算")
        self.calls += 1
        reply = self.llm.chat(messages, json_output=True, max_tokens=max_output, thinking=False)
        usage = reply.get("usage") or {}
        total = usage.get("total_tokens")
        if not isinstance(total, int) or total < 0:
            raise ValueError("模型没有返回可核算的 Token 用量")
        self.tokens += total
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int):
            self.cost += (prompt * input_rate + completion * output_rate) / 1_000_000
        else:
            self.cost += total * max(input_rate, output_rate) / 1_000_000
        if self.tokens > MAX_TOTAL_TOKENS or self.cost > cap:
            raise ValueError("反方研究实际 Token 或费用超过预算")
        content = reply.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("模型缺少 JSON 内容")
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("模型没有返回 JSON 对象")
        return raw

    @staticmethod
    def _packet(graph: EventGraphRun) -> dict:
        citations = {item.citation_id: item for item in graph.citations}
        sources = {item.document_id: item for item in graph.sources}
        return {"question": graph.question, "subject_code": graph.subject_code,
                "window_start": graph.window_start.isoformat(),
                "as_of_date": graph.as_of_date.isoformat(),
                "graph_status": graph.status, "warnings": graph.warnings,
                "events": [{"event_id": event.event_id, "title": event.title,
                            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                            "date_status": event.date_status, "conflict_ids": event.conflict_ids}
                           for event in graph.events],
                "claims": [{"claim_id": f"c{index}", "event_id": claim.event_id,
                            "attribute": claim.attribute, "value_text": claim.value_text,
                            "numeric_status": claim.numeric_status,
                            "quote": citations[claim.citation_id].quote[:200],
                            "document_id": citations[claim.citation_id].document_id,
                            "source_title": sources[citations[claim.citation_id].document_id].title,
                            "published_at": sources[citations[claim.citation_id].document_id].published_at.isoformat()}
                           for index, claim in enumerate(graph.claims, 1)],
                "conflicts": [{**item.model_dump(mode="json"),
                               "claim_ids": [f"c{next(index for index, claim in enumerate(graph.claims, 1) if claim.claim_id == claim_id)}"
                                             for claim_id in item.claim_ids]}
                              for item in graph.conflicts]}

    def _short_hypotheses(self, hypotheses: list[DebateHypothesis]) -> list[dict]:
        return [{**hypothesis.model_dump(),
                 "support_claim_ids": [self.claim_to_alias.get(value, value)
                                       for value in hypothesis.support_claim_ids],
                 "counter_claim_ids": [self.claim_to_alias.get(value, value)
                                       for value in hypothesis.counter_claim_ids]}
                for hypothesis in hypotheses]

    def _derived_missing(self, graph: EventGraphRun, support: list[str]) -> list[str]:
        """仅说明已保存图谱能够确认的缺口，不采用模型对证据缺失的猜测。"""
        claims = {item.claim_id: item for item in graph.claims}
        events = {item.event_id: item for item in graph.events}
        citations = {item.citation_id: item for item in graph.citations}
        sources = {item.document_id: item for item in graph.sources}
        referenced = [claims[item] for item in support]
        if not referenced:
            return ["该候选解释没有已引用的支持主张，需要补充可定位的原文。"]
        gaps: list[str] = []
        if any(events[item.event_id].date_status != "known" for item in referenced):
            gaps.append("本次图谱中部分所引事件没有可确认的发生日，不能用资料发布日期替代。")
        if any(item.numeric_status == "calculation_mismatch" or
               events[item.event_id].conflict_ids for item in referenced):
            gaps.append("本次图谱中部分所引数值或日期仍有未解释的冲突，需要核对原文口径。")
        families = {self._source_family(citations[item.citation_id].document_id, sources)
                    for item in referenced}
        if len(families) < 2:
            gaps.append("本次图谱的支持主张只来自一组独立来源，需要交叉核对。")
        if not any(sources[citations[item.citation_id].document_id].source_type == "announcement"
                   for item in referenced):
            gaps.append("当前支持主张未引用独立公告，需要核对公司或交易所原文。")
        if MARKET_QUESTION.search(graph.question):
            gaps.append("仍需核对同期行情、行业与市场因素，不能仅凭事件解释价格变化。")
        return gaps or ["仍需检索可能的反例；本次未发现反证不等于反证已被排除。"]

    @staticmethod
    def _verify_graph_bytes(repo: SQLiteRepository, graph: EventGraphRun) -> list[str]:
        issues = []
        for source in graph.sources:
            if source.snapshot_path:
                snapshot = Path(source.snapshot_path)
                if not snapshot.is_file() or sha256(snapshot.read_bytes()).hexdigest() != source.content_hash:
                    issues.append(f"来源 {source.document_id} 的原始快照哈希不一致")
        for citation in graph.citations:
            row = repo.conn.execute(
                "SELECT c.content,d.subject_ids,d.published_at,d.source_id,d.source_url "
                "FROM document_chunks c JOIN documents d ON c.document_id=d.document_id "
                "WHERE c.chunk_id=? AND c.document_id=?",
                (citation.chunk_id, citation.document_id)).fetchone()
            source = next((s for s in graph.sources if s.document_id == citation.document_id), None)
            if (row is None or source is None or
                    row["content"][citation.start_offset:citation.end_offset] != citation.quote or
                    graph.subject_code not in json.loads(row["subject_ids"] or "[]") or
                    row["published_at"] != source.published_at.isoformat() or
                    row["source_id"] != source.source_id or row["source_url"] != source.source_url):
                issues.append(f"引用 {citation.citation_id} 无法回到当前范围内的原文")
        citations = {item.citation_id: item for item in graph.citations}
        claims = {item.claim_id: item for item in graph.claims}
        for event in graph.events:
            if event.date_status != "known" or event.occurred_at is None:
                continue
            if not any(_occurrence_date_supported(citations[claims[claim_id].citation_id].quote,
                                                  event.occurred_at)
                       for claim_id in event.claim_ids):
                issues.append(f"事件 {event.event_id} 的发生日期只有落款或无动作原文")
        return issues

    def _propose(self, packet: dict, graph: EventGraphRun) -> list[DebateHypothesis]:
        raw = self._ask(
            "你是假设分析 Agent。只返回 JSON 对象 {\"hypotheses\":[]}。最多三个不同候选解释；"
            "每项仅含 statement、support_claim_ids、counter_claim_ids、missing_evidence、alternative_explanations。"
            "每项最多引用12条支持主张、12条反对主张，最多列6项缺失证据和5项替代解释。"
            "优先引用关键事实，不要把输入中的每条主张都重复罗列。"
            "问题若只询问事件日期，不提出股价影响判断。公告计划日和统计截止日不是已完成动作的发生日；"
            "公告所列预定上市流通日即使早于研究截止日，也不能写成实际已完成上市流通。"
            "published_at 是资料库记录的文档发布日期，不等同于报告正文确认的披露日期。"
            "仅由媒体记载的披露时点必须写成‘媒体报道’，不得说成公司原文已确认。"
            "缺失证据要先与全部输入主张比对；已有基数、日期或数字不可再声称缺失。"
            "所有 claim ID 必须来自输入；不得补造数字、日期或确定的股价因果。"
            "支持不足也要诚实列出缺失证据；因果问题尽量给出至少两个竞争性解释。",
            packet, 1800)
        items = raw.get("hypotheses")
        if not isinstance(items, list) or len(items) > 3:
            raise ValueError("候选解释数量无效")
        known = {item.claim_id for item in graph.claims}
        result = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"statement", "support_claim_ids",
                    "counter_claim_ids", "missing_evidence", "alternative_explanations"}:
                self.protocol_warnings.append("模型候选解释字段不完整，已丢弃该项")
                continue
            statement = item["statement"]
            if not isinstance(statement, str) or not statement.strip() or len(statement) > 350:
                raise ValueError("候选解释陈述无效")
            if re.search(r"明确发生日期.{0,30}?(?:截至|截止日|统计截止日)", statement):
                statement = re.sub(r"明确发生日期(?:的是|包括|有)",
                                   "可核对的日期（统计截止日不是动作发生日）包括", statement,
                                   count=1)
                self.protocol_warnings.append("候选解释混淆统计截止日与发生日，已标明日期性质")
            if FORBIDDEN_CERTAINTY.search(statement):
                raise ValueError("候选解释含确定性因果表述")
            key = re.sub(r"\W+", "", statement).lower()
            if key in seen:
                raise ValueError("候选解释重复")
            seen.add(key)
            bounded = {}
            for field, maximum in (("support_claim_ids", 12), ("counter_claim_ids", 12),
                                   ("missing_evidence", 6), ("alternative_explanations", 5)):
                value = item[field]
                if isinstance(value, list) and len(value) > maximum:
                    self.protocol_warnings.append(f"候选解释 {len(result)+1} 的{field}超出上限，已截取前{maximum}项")
                    value = value[:maximum]
                bounded[field] = value
            support = [self.alias_to_claim.get(value, value) for value in
                       _unique_texts(bounded["support_claim_ids"], maximum=12, length=100)]
            counter = [self.alias_to_claim.get(value, value) for value in
                       _unique_texts(bounded["counter_claim_ids"], maximum=12, length=100)]
            invalid = set(support + counter) - known
            if invalid:
                raise ValueError("候选解释引用了未保存的主张编号")
            overlap = set(support) & set(counter)
            if overlap:
                self.protocol_warnings.append(f"候选解释 {len(result)+1} 有同时支持和反对的主张，已仅保留为反对证据")
                support = [claim_id for claim_id in support if claim_id not in overlap]
            claim_map = {claim.claim_id: claim for claim in graph.claims}
            citation_map = {citation.citation_id: citation for citation in graph.citations}
            source_map = {source.document_id: source for source in graph.sources}
            dated_segments = re.split(r"[；。]", statement)
            unsupported_completion = False
            for segment in dated_segments:
                if not ("披露" in segment and
                        re.search(r"正式披露|完成.{0,8}披露|已[在于].{0,50}披露|"
                                  r"于.{0,20}(?:披露|发布)", segment)):
                    continue
                for parts in DATE_RE.findall(segment):
                    dated = tuple(map(int, parts))
                    official = any(
                        tuple(map(int, match)) == dated and
                        source_map[citation.document_id].source_type != "news" and
                        any(word in citation.quote for word in ("披露", "公布", "发布"))
                        for claim_id in support
                        for citation in [citation_map[claim_map[claim_id].citation_id]]
                        for match in DATE_RE.findall(citation.quote))
                    if not official:
                        unsupported_completion = True
            if unsupported_completion:
                self.protocol_warnings.append("候选解释把仅由媒体记载的披露时点写成已完成动作，已丢弃")
                continue
            cited_sources = {citation_map[claim_map[claim_id].citation_id].document_id
                             for claim_id in support}
            source_families = {self._source_family(document_id, source_map)
                               for document_id in cited_sources}
            statement = statement.replace("新闻通稿（媒体报道口径）", "新闻通稿（公司发布口径）")
            statement = statement.replace("依据新闻通稿正文，属媒体报道口径", "依据公司通稿正文")
            if (re.search(r"独立来源|独立印证|相互印证", statement)
                    and len(source_families) < 2):
                self.protocol_warnings.append("候选解释将同一财务披露的转述当作独立印证，已丢弃")
                continue
            if (re.search(r"(?:均|全部).{0,20}明确发生日期", statement)
                    and any(next(event for event in graph.events
                                  if event.event_id == claim_map[claim_id].event_id).date_status != "known"
                            for claim_id in support)):
                self.protocol_warnings.append("候选解释把图谱中日期未知的事项写成全部有明确发生日，已丢弃")
                continue
            supported_events = [next(event for event in graph.events
                                     if event.event_id == claim_map[claim_id].event_id)
                                for claim_id in support]
            if (re.search(r"(?:有明确发生日期|明确动作日期|存在可核验的披露日期)", statement)
                    and any(event.date_status != "known" for event in supported_events)
                    and not re.search(r"(?:缺少|没有|无)明确发生日期", statement)):
                self.protocol_warnings.append("候选解释把未确认日期称为明确发生日，已丢弃")
                continue
            if (any(event.date_status != "known" for event in supported_events)
                    and "推定" not in statement
                    and re.search(r"(?:均|全部).{0,35}(?:该日|20\d{2}年\d{1,2}月\d{1,2}日)"
                                  r".{0,20}(?:完成|发生|通过)", statement)):
                self.protocol_warnings.append("候选解释把日期未知的议案写成同日完成，已丢弃")
                continue
            alternatives = _unique_texts(bounded["alternative_explanations"],
                                         maximum=5, length=300)
            if any(conflict.kind == "calculation" for conflict in graph.conflicts):
                alternatives = [value for value in alternatives
                                if not ("四舍五入" in value and "差异" in value)]
            result.append(DebateHypothesis(hypothesis_id=f"h{len(result)+1}",
                statement=_plain_text(statement.strip()), support_claim_ids=support,
                counter_claim_ids=counter,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=[_plain_text(value) for value in alternatives],
                assessment="insufficient"))
        if not result:
            result = self._fallback_hypotheses(graph)
            self.protocol_warnings.append("模型候选解释均未通过证据门禁，已使用图谱事实生成保守候选")
        if CAUSAL_QUESTION.search(graph.question) and len(result) == 1:
            result.append(DebateHypothesis(hypothesis_id="h2",
                statement="行业或市场共同变化也可能参与解释观察到的现象",
                missing_evidence=["需要独立核对行业与市场同期变化"],
                alternative_explanations=[result[0].statement], assessment="insufficient"))
        return result

    def _fallback_hypotheses(self, graph: EventGraphRun) -> list[DebateHypothesis]:
        """模型输出被拒绝后，依据已校验的图谱状态构建可供复核的候选解释。"""
        result: list[DebateHypothesis] = []
        known = [event for event in graph.events
                 if event.date_status == "known" and event.occurred_at is not None]
        unknown = [event for event in graph.events if event.date_status != "known"]
        planned = [event for event in graph.events
                   if re.search(r"拟|计划|预定|预计", event.title)]

        if known:
            summary = "；".join(f"{event.title}为{event.occurred_at.isoformat()}"
                               for event in known[:4])
            support = list(dict.fromkeys(
                claim_id for event in known for claim_id in event.claim_ids))[:12]
            result.append(DebateHypothesis(
                hypothesis_id=f"h{len(result)+1}",
                statement=f"图谱原文可直接核对的动作日期包括：{summary}。",
                support_claim_ids=support,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=["资料库发布日期只能辅助定位，不能替代正文动作日期"],
                assessment="insufficient"))
        if unknown:
            support = list(dict.fromkeys(
                claim_id for event in unknown for claim_id in event.claim_ids))[:12]
            result.append(DebateHypothesis(
                hypothesis_id=f"h{len(result)+1}",
                statement=(f"图谱中另有{len(unknown)}个事项缺少经动作原文确认的发生日期；"
                           "当前只能保留为日期未知，不能用统计截止日或资料库发布日期补齐。"),
                support_claim_ids=support,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=["相关日期可能存在于本次资料库未收录的独立公告或附件"],
                assessment="insufficient"))
        if planned:
            support = list(dict.fromkeys(
                claim_id for event in planned for claim_id in event.claim_ids))[:12]
            result.append(DebateHypothesis(
                hypothesis_id=f"h{len(result)+1}",
                statement=("公告中的计划或预定日期只证明披露时的安排；在缺少完成确认文件时，"
                           "不能据此认定相应动作已经发生。"),
                support_claim_ids=support,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=["计划可能按期完成，也可能随后变更，需以后续确认文件为准"],
                assessment="insufficient"))
        elif graph.conflicts:
            support = list(dict.fromkeys(
                claim_id for conflict in graph.conflicts for claim_id in conflict.claim_ids))[:12]
            result.append(DebateHypothesis(
                hypothesis_id=f"h{len(result)+1}",
                statement="图谱保留的数值或日期冲突尚未得到原文口径解释，不能据此生成确定结论。",
                support_claim_ids=support,
                missing_evidence=self._derived_missing(graph, support),
                alternative_explanations=["差异可能来自统计口径，也可能来自转述或计算错误"],
                assessment="insufficient"))
        return result[:3]

    def _search(self, repo: SQLiteRepository, packet: dict,
                hypotheses: list[DebateHypothesis], graph: EventGraphRun) -> list[DebateSearchCheck]:
        raw = self._ask(
            "你是反方检索规划 Agent。只返回 JSON 对象 {\"queries\":[]}，最多三个简短检索词。"
            "针对遗漏事件、相反数字、转载依赖、时间先后；不得修改公司或日期，不得请求写入或联网。",
            {"evidence": packet, "hypotheses": self._short_hypotheses(hypotheses)}, 700)
        queries = _unique_texts(raw.get("queries"), maximum=3, length=40)
        checks = []
        for query in queries:
            hits = repo.search_event_chunks(query, graph.subject_code, graph.as_of_date,
                window_start=graph.window_start, limit=MAX_SEARCH_HITS)
            checks.append(DebateSearchCheck(query=query, hits=[DebateSearchHit(
                chunk_id=hit.chunk.chunk_id, document_id=hit.chunk.document_id,
                source_id=hit.chunk.source_id, title=hit.chunk.title,
                published_at=hit.chunk.published_at, excerpt=hit.chunk.content[:350])
                for hit in hits]))
        return checks

    def _counter(self, packet: dict, hypotheses: list[DebateHypothesis],
                 checks: list[DebateSearchCheck], graph: EventGraphRun) -> list[DebateFinding]:
        raw = self._ask(
            "你是反方核验 Agent。只返回 JSON 对象 {\"findings\":[]}，最多五项，每项detail不超过150字。"
            "每项仅含 hypothesis_id、kind、detail、claim_ids、hit_chunk_ids。"
            "kind 用 chronology、contradiction、source_dependence、omission、scope、alternative、unresolved。"
            "主动指出支持解释的薄弱环节；检索不到反证不能写成已排除反证。"
            "资料发布日期与事件发生日不同不自动构成矛盾；数值按披露精度四舍五入后相容则不写冲突。"
            "正常的先后顺序本身不是疑点；原文明确列示的预定上市日不能写成已实际上市。"
            "只有具体主张或检索命中支持的内容才能写入核验发现。"
            "原文明确写‘会议召开’时不能无依据猜测它只是表决截止日。"
            "声称资料缺少某事实前须检查检索命中；仅查到部分片段时只能说‘本次所引片段未见’。"
            "引用 ID 只能来自输入，不能把检索片段中尚未提取的内容当成已确认事实。",
            {"evidence": packet, "hypotheses": self._short_hypotheses(hypotheses),
             "search_checks": [c.model_dump(mode="json") for c in checks]}, 2300)
        items = raw.get("findings")
        if not isinstance(items, list) or len(items) > 5:
            raise ValueError("反方意见数量无效")
        known_h = {h.hypothesis_id for h in hypotheses}
        known_c = {c.claim_id for c in graph.claims}
        known_hits = {hit.chunk_id for check in checks for hit in check.hits}
        findings = []
        for item in items:
            if not isinstance(item, dict) or not {"hypothesis_id", "kind", "detail"} <= set(item):
                raise ValueError("反方意见字段无效")
            if item["hypothesis_id"] not in known_h or item["kind"] not in FINDING_KINDS:
                raise ValueError("反方意见的目标或类型无效")
            claim_ids = [self.alias_to_claim.get(value, value) for value in
                         _unique_texts(item.get("claim_ids", []), maximum=12, length=100)]
            hit_ids = _unique_texts(item.get("hit_chunk_ids", []), maximum=12, length=100)
            if not set(claim_ids) <= known_c or not set(hit_ids) <= known_hits:
                raise ValueError("反方意见引用了未保存的资料")
            kind, detail = item["kind"], item["detail"]
            if not isinstance(detail, str) or not 1 <= len(detail.strip()) <= 300:
                raise ValueError("反方意见内容无效")
            if kind == "contradiction" and re.search(r"不构成(?:事实)?(?:矛盾|冲突)|并非(?:矛盾|冲突)", detail):
                kind = "scope"
                self.protocol_warnings.append("反方意见文字否认矛盾，已取消冲突标签")
            if kind == "chronology" and re.search(r"相隔较长|是否同日", detail):
                kind = "unresolved"
                self.protocol_warnings.append("反方意见只提出时间疑问，已取消时序冲突标签")
            claim_map = {claim.claim_id: claim for claim in graph.claims}
            citation_map = {citation.citation_id: citation for citation in graph.citations}
            evidence_text = " ".join(
                [citation_map[claim_map[cid].citation_id].quote for cid in claim_ids] +
                [hit.excerpt for check in checks for hit in check.hits if hit.chunk_id in hit_ids])
            mentioned_dates = {tuple(map(int, parts)) for parts in DATE_RE.findall(detail)}
            anchored_dates = {tuple(map(int, parts)) for parts in DATE_RE.findall(evidence_text)}
            if not mentioned_dates <= anchored_dates:
                self.protocol_warnings.append("反方意见含未由所引原文支持的具体日期，已丢弃")
                continue
            if ("唯一" in detail and any(
                    parts < (graph.window_start.year, graph.window_start.month, graph.window_start.day)
                    or parts > (graph.as_of_date.year, graph.as_of_date.month, graph.as_of_date.day)
                    for parts in mentioned_dates)
                    and any("窗口内" in hypothesis.statement
                            for hypothesis in hypotheses
                            if hypothesis.hypothesis_id == item["hypothesis_id"])):
                self.protocol_warnings.append("反方用研究窗口外的日期反驳窗口内判断，已丢弃")
                continue
            if (kind == "omission" and re.search(r"未收录|遗漏|缺失", detail)
                    and mentioned_dates and all(
                        parts < (graph.window_start.year, graph.window_start.month,
                                 graph.window_start.day)
                        or parts > (graph.as_of_date.year, graph.as_of_date.month,
                                    graph.as_of_date.day)
                        for parts in mentioned_dates)):
                self.protocol_warnings.append("反方把窗口外历史事项当成当前事件遗漏，已丢弃")
                continue
            if (re.search(r"彼此独立|两处独立|相互独立|独立来源", detail)
                    and len({self._source_family(
                        citation_map[claim_map[claim_id].citation_id].document_id,
                        {source.document_id: source for source in graph.sources})
                        for claim_id in claim_ids}) < 2):
                self.protocol_warnings.append("反方把可能同源的财务报道当作独立来源，已丢弃")
                continue
            if kind == "contradiction" and len(claim_ids) == 2:
                claim_map = {claim.claim_id: claim for claim in graph.claims}
                left, right = (claim_map[claim_id] for claim_id in claim_ids)
                meanings = {claim_id: _number(claim_map[claim_id].value_text or "")
                            for claim_id in claim_ids}
                if (left.attribute == right.attribute and all(meanings.values())
                        and _numbers_agree(left, right, meanings)):
                    kind = "unresolved"
                    detail = ("所引两项数值按各自披露精度可以相容，不能仅据此判为数值冲突；"
                              "统计口径仍可继续核对。")
                    self.protocol_warnings.append("反方将舍入后相容的数值误判为冲突，已更正")
            findings.append(DebateFinding(hypothesis_id=item["hypothesis_id"],
                kind=kind, detail=_plain_text(detail), claim_ids=claim_ids,
                hit_chunk_ids=hit_ids))
        if not findings and hypotheses:
            findings = self._fallback_findings(hypotheses)
        return findings

    def _fallback_findings(self, hypotheses: list[DebateHypothesis]) -> list[DebateFinding]:
        findings = []
        for hypothesis in hypotheses[:5]:
            missing = hypothesis.missing_evidence[0].rstrip("。；") if hypothesis.missing_evidence else ""
            if missing:
                detail = f"仍需核对：{missing}；当前证据不足以排除其他解释。"
                kind = "unresolved"
            elif hypothesis.alternative_explanations:
                detail = (f"仍需核对替代解释：{hypothesis.alternative_explanations[0].rstrip('。；')}；"
                          "当前材料不能排除该可能。")
                kind = "alternative"
            else:
                detail = "现有材料未形成独立交叉验证，当前结论只能保留为待核。"
                kind = "unresolved"
            findings.append(DebateFinding(
                hypothesis_id=hypothesis.hypothesis_id, kind=kind,
                detail=detail[:300], claim_ids=hypothesis.support_claim_ids[:12],
                hit_chunk_ids=[]))
        self.protocol_warnings.append("反方未形成可核验结果，已按缺失证据生成保守核查项")
        return findings

    def _report_draft(self, packet: dict, hypotheses: list[DebateHypothesis],
                      findings: list[DebateFinding]) -> tuple[str, str | None]:
        raw = self._ask(
            "你是报告 Agent。只返回 JSON 对象，且仅含 draft_conclusion、selected_hypothesis_id。"
            "简短说明哪种解释值得继续核查以及未知项，不补造事实，不写确定性因果、预测或建议。"
            "不能将预定上市流通日写成实际完成上市，不要输出 c1 等内部主张编号。"
            "selected_hypothesis_id 只能是输入假设 ID 或 null。",
            {"question": packet["question"], "hypotheses": self._short_hypotheses(hypotheses),
             "findings": [f.model_dump() for f in findings]}, 1200)
        required = {"draft_conclusion", "selected_hypothesis_id"}
        if not required <= set(raw):
            self.protocol_warnings.append("报告草稿缺少必填字段，已使用保守的结构化摘要")
            return (f"已形成{len(hypotheses)}个候选解释和{len(findings)}条反方核验记录；"
                    "报告草稿未通过格式检查，当前不形成具体事实判断。", None)
        if set(raw) != required:
            self.protocol_warnings.append("报告草稿包含额外字段，已忽略")
        draft = raw["draft_conclusion"]
        chosen = raw["selected_hypothesis_id"]
        if not isinstance(draft, str) or len(draft) > 600 or FORBIDDEN_CERTAINTY.search(draft):
            raise ValueError("报告草稿包含无效或确定性因果表述")
        if re.search(r"(?<![A-Za-z0-9])c[A-Za-z]*\d+(?![A-Za-z0-9])", draft):
            self.protocol_warnings.append("报告草稿含内部主张编号，已移除编号")
        if re.search(r"(?<![A-Za-z0-9])h\d+(?![A-Za-z0-9])", draft):
            self.protocol_warnings.append("报告草稿含内部假设编号，已移除编号")
        if (re.search(r"(?:两项|相关)?来源各自独立", draft)
                and re.search(r"独立性不足|同源|同一(?:篇|份|个).{0,12}(?:通稿|公告|文档)", draft)):
            self.protocol_warnings.append("报告草稿对来源独立性的表述前后矛盾，已移除肯定表述")
            draft = re.sub(r"(?:但)?(?:两项|相关)?来源各自独立[；，。]?", "", draft)
        if "四舍五入" in draft and any(item.get("kind") == "calculation"
                                        for item in packet["conflicts"]):
            self.protocol_warnings.append("报告草稿未经证据支持地用四舍五入解释数值冲突，已改为保守摘要")
            draft = (f"已提出{len(hypotheses)}个候选解释和{len(findings)}条反方意见。"
                     "图谱仍有待核实的数值差异，原因尚未确认；请逐项查看原文引用。")
        if chosen is not None and chosen not in {h.hypothesis_id for h in hypotheses}:
            raise ValueError("报告草稿选择了不存在的解释")
        return _plain_text(draft.strip()), chosen

    @staticmethod
    def _source_family(document_id: str, sources: dict) -> str:
        seen = set()
        while document_id in sources and sources[document_id].reprint_of and document_id not in seen:
            seen.add(document_id)
            document_id = sources[document_id].reprint_of
        source = sources.get(document_id)
        if source and source.source_type == "news":
            financial = next((item for item in sources.values()
                              if item.source_type == "financial"), None)
            if financial is not None:
                # 在当前语料中，新闻对公司财务数据的转述，
                # 不能视为对同一组数据的独立确认。
                return financial.content_hash
        return (source.content_hash if source else document_id) or document_id

    def _quality_gate(self, graph: EventGraphRun, hypotheses: list[DebateHypothesis],
                      findings: list[DebateFinding], market_rows: list[dict],
                      issues: list[str], chosen: str | None) -> DebateConclusion:
        claims = {c.claim_id: c for c in graph.claims}
        citations = {c.citation_id: c for c in graph.citations}
        sources = {s.document_id: s for s in graph.sources}
        events = {e.event_id: e for e in graph.events}
        causal = bool(CAUSAL_QUESTION.search(graph.question))
        market = bool(MARKET_QUESTION.search(graph.question))
        serious = {"chronology", "contradiction", "source_dependence", "scope", "unresolved"}
        if hypotheses and not findings:
            issues.append("反方未形成可核验的核查结果")
        if not any(source.source_type == "announcement" for source in graph.sources):
            issues.append("图谱缺少独立公告原文，真实资料覆盖不足")
        for hypothesis in hypotheses:
            refs = [claims[item] for item in hypothesis.support_claim_ids]
            source_families = {self._source_family(
                citations[claim.citation_id].document_id, sources) for claim in refs}
            related = [f for f in findings if f.hypothesis_id == hypothesis.hypothesis_id]
            no_date = any(events[claim.event_id].date_status != "known" for claim in refs)
            conflict = any(claim.numeric_status == "calculation_mismatch" or
                           events[claim.event_id].conflict_ids for claim in refs)
            values_by_key: dict[tuple[str, str], set[str]] = {}
            for claim in refs:
                if claim.value_text:
                    values_by_key.setdefault((claim.event_id, claim.attribute), set()).add(claim.value_text)
            if any(len(values) > 1 for values in values_by_key.values()):
                conflict = True
            if not refs:
                hypothesis.assessment = "insufficient"
            elif market and (no_date or not market_rows):
                hypothesis.assessment = "cannot_judge"
                issues.append(f"{hypothesis.hypothesis_id} 缺少可确认的事件发生日期或同期行情")
            elif conflict or hypothesis.counter_claim_ids or any(f.kind in serious for f in related):
                hypothesis.assessment = "insufficient"
                issues.append(f"{hypothesis.hypothesis_id} 存在冲突或尚未排除的反方问题")
            elif len(source_families) < 2:
                hypothesis.assessment = "associated" if not causal else "insufficient"
                issues.append(f"{hypothesis.hypothesis_id} 只有一组独立来源")
            elif causal:
                hypothesis.assessment = "associated"
            else:
                hypothesis.assessment = "evidence_strong"
        if graph.status != "extracted":
            issues.append("上游事件图谱尚未通过完整提取")
        if graph.warnings:
            issues.append("上游事件图谱存在未解决的警告")
        if graph.status != "extracted" or graph.warnings:
            for hypothesis in hypotheses:
                if hypothesis.assessment == "evidence_strong":
                    hypothesis.assessment = "associated"
        levels = [h.assessment for h in hypotheses]
        if not levels or ("cannot_judge" in levels and
                          not any(level in {"evidence_strong", "associated"} for level in levels)):
            level = "cannot_judge"
        elif "evidence_strong" in levels:
            level = "evidence_strong"
        elif "associated" in levels:
            level = "associated"
        else:
            level = "insufficient"
        if issues or graph.status != "extracted":
            status = "partial" if hypotheses else "insufficient_evidence"
        elif level in {"evidence_strong", "associated"}:
            status = "answered"
        else:
            status = "insufficient_evidence"
        chosen_item = next((h for h in hypotheses if h.hypothesis_id == chosen), None)
        if chosen_item is None or chosen_item.assessment in {"cannot_judge", "insufficient"}:
            chosen_item = next((h for h in hypotheses
                                if h.assessment in {"evidence_strong", "associated"}), None)
        if level == "cannot_judge":
            text = "现有原文、事件日期或行情不足，无法判断候选解释与所问现象的先后关系。"
        elif level == "insufficient":
            text = "已列出需要检验的候选解释，但支持材料不足或存在尚未解决的反证，不能确定原因。"
        elif status == "partial":
            text = "候选解释有部分可核对的相关证据，但图谱或反方核验仍有未解决的问题，暂不能形成完整判断。"
        else:
            text = (f"候选解释「{chosen_item.statement}」有可核对的相关证据；"
                    "这仍是研究假设，不能据此证明市场因果。")
        return DebateConclusion(level=level, answer_status=status, text=text,
            selected_hypothesis_id=chosen_item.hypothesis_id if chosen_item else None,
            limitations=list(dict.fromkeys(issues)))

    def build(self, graph_run_id: str) -> EventDebateRun:
        if not self.llm.configured:
            raise RuntimeError("尚未配置模型，无法生成竞争性假设")
        graph_store = EventGraphStore(self.graph_database, readonly=True)
        try:
            graph = graph_store.get(graph_run_id)
        finally:
            graph_store.close()
        if graph is None:
            raise ValueError("事件图谱运行不存在")
        self.alias_to_claim = {f"c{index}": claim.claim_id
                               for index, claim in enumerate(graph.claims, 1)}
        self.claim_to_alias = {real: short for short, real in self.alias_to_claim.items()}
        source_sha256 = sha256(self.source_database.read_bytes()).hexdigest()
        repo = SQLiteRepository.open_readonly(self.source_database)
        self.started = monotonic()
        hypotheses: list[DebateHypothesis] = []
        checks: list[DebateSearchCheck] = []
        findings: list[DebateFinding] = []
        issues: list[str] = []
        draft, chosen = "", None
        stop_reason = "完成反方核验"
        try:
            issues.extend(self._verify_graph_bytes(repo, graph))
            market_rows = repo.company_market(graph.subject_code, graph.window_start,
                                              graph.as_of_date, limit=31)
            packet = self._packet(graph)
            packet["market_context"] = [{"trade_date": row["trade_date"],
                "close": row["close"], "source": row["source"]} for row in market_rows[:3]]
            if issues:
                stop_reason = "图谱引用无法核对，已停止模型研究"
            elif graph.events and graph.claims:
                try:
                    hypotheses = self._propose(packet, graph)
                    checks = self._search(repo, packet, hypotheses, graph)
                    findings = self._counter(packet, hypotheses, checks, graph)
                    draft, chosen = self._report_draft(packet, hypotheses, findings)
                except (ValueError, RuntimeError, json.JSONDecodeError, requests.RequestException) as exc:
                    issues.append(f"模型研究中止：{type(exc).__name__}: {exc}")
                    stop_reason = "模型研究失败或超过预算，保留已有结果"
                    if hypotheses and not findings:
                        findings = self._fallback_findings(hypotheses)
                    if hypotheses and not draft:
                        draft = (f"已保留{len(hypotheses)}个候选解释和{len(findings)}条保守核查项；"
                                 "模型核验未完成，当前不能形成完整判断。")
            else:
                stop_reason = "图谱没有可用事件和主张"
            issues.extend(self.protocol_warnings)
            conclusion = self._quality_gate(graph, hypotheses, findings, market_rows, issues, chosen)
            if stop_reason != "完成反方核验":
                conclusion.level = "insufficient" if hypotheses else "cannot_judge"
                conclusion.answer_status = "partial" if hypotheses else "insufficient_evidence"
                conclusion.selected_hypothesis_id = None
                conclusion.text = "模型核验中止，已保留可追溯的候选解释；当前不能形成完整判断。"
                conclusion.limitations = list(dict.fromkeys(issues))
            run = EventDebateRun(run_id=f"debate-{uuid4().hex}", graph_run_id=graph.run_id,
                graph=graph, source_database_sha256=source_sha256,
                created_at=datetime.now(timezone.utc),
                model=str(getattr(self.llm, "model", "unknown")), model_tokens=self.tokens,
                estimated_cost_usd=self.cost, stop_reason=stop_reason,
                hypotheses=hypotheses, search_checks=checks, findings=findings,
                conclusion=conclusion, quality_issues=list(dict.fromkeys(issues)),
                report_draft=draft)
            store = EventDebateStore(self.debate_database)
            try:
                store.save(run)
            finally:
                store.close()
            return run
        finally:
            repo.close()
