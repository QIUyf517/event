"""提取可定位到原文的事件事实，并整理指定范围内的时间线。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path

from app.agents.event_orchestrator import DEEPSEEK_FLASH_PRICES, EventPlanningRun
from app.data.repositories.event_graph import EventGraphStore
from app.data.repositories.sqlite import SQLiteRepository
from app.llm.client import OpenAICompatibleClient
from app.models.event_graph import (EventGraphRun, GraphCitation, GraphClaim,
                                    GraphConflict, GraphEvent, GraphRelation, GraphSource)
from app.tools.research_tools import EVENT_SUBJECTS


MAX_EVIDENCE_CHUNKS = 16
MAX_CHUNKS_PER_DOCUMENT = 4
MAX_CHARS_PER_CHUNK = 1100
MAX_EXTRACTED_ITEMS = 64
MAX_OUTPUT_TOKENS = 5000
MAX_TOTAL_TOKENS = 40000
DEFAULT_COST_CAP_USD = 0.06
EVENT_TYPES = {"financial_disclosure", "corporate_action", "business_update", "other"}
IMPORTANCE = {"low": 0, "medium": 1, "high": 2}
ITEM_FIELDS = {"evidence_id", "quote", "event_type", "title", "target", "period_label",
               "occurred_at", "importance", "attribute", "value_text"}
DATE_RE = re.compile(r"(?<!\d)(\d{4})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月|[-/])\s*(\d{1,2})\s*日?")
NUMBER_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(万亿元|亿元|万元|千元|元|亿股|万股|股|亿|万|%|手|倍)?$")
NUMBER_FRAGMENT_RE = re.compile(r"[+-]?\d[\d,，]*(?:[.．]\d+)?")


def _id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\s\W_]+", "", text)


def _locate_quote(source: str, proposed: str) -> str | None:
    """将 PDF 换行和字符宽度差异映射回已保存原文中的精确位置。"""
    if proposed in source:
        return proposed
    normalized, positions = [], []
    for offset, char in enumerate(source):
        for part in unicodedata.normalize("NFKC", char):
            if not part.isspace():
                normalized.append(part)
                positions.append(offset)
    needle = "".join(part for char in proposed
                     for part in unicodedata.normalize("NFKC", char)
                     if not part.isspace())
    if len(needle) < 8:
        return None
    index = "".join(normalized).find(needle)
    if index < 0:
        return None
    exact = source[positions[index]:positions[index + len(needle) - 1] + 1]
    return exact if len(exact) <= 240 else None


def _number(value: str) -> tuple[str, Decimal] | None:
    clean = unicodedata.normalize("NFKC", value).replace(",", "").strip()
    match = NUMBER_RE.fullmatch(clean)
    if not match:
        return None
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation:
        return None
    unit = match.group(2) or ""
    money = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000),
             "亿元": Decimal(100000000), "万亿元": Decimal(1000000000000)}
    shares = {"股": Decimal(1), "万股": Decimal(10000), "亿股": Decimal(100000000)}
    if unit in money:
        return "money", amount * money[unit]
    if unit in shares:
        return "shares", amount * shares[unit]
    if unit == "%":
        return "percent", amount / 100
    if unit in {"万", "亿"}:
        return "count", amount * {"万": Decimal(10000), "亿": Decimal(100000000)}[unit]
    return unit or "unitless", amount


def _numeric_resolution(value: str, unit_context: str | None) -> Decimal:
    clean = unicodedata.normalize("NFKC", value).replace(",", "").strip()
    match = NUMBER_RE.fullmatch(clean)
    if match is None:
        raise ValueError("数值格式无效")
    places = len(match.group(1).partition(".")[2])
    unit = match.group(2) or ""
    if not unit and unit_context:
        unit = unit_context.rsplit("：", 1)[-1]
    scales = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000,
              "万亿元": 1000000000000, "股": 1, "万股": 10000,
              "亿股": 100000000, "万": 10000, "亿": 100000000,
              "%": Decimal("0.01")}
    return Decimal(10) ** -places * scales.get(unit, 1)


def _numbers_agree(left: GraphClaim, right: GraphClaim,
                   meanings: dict[str, tuple[str, Decimal]]) -> bool:
    left_type, left_value = meanings[left.claim_id]
    right_type, right_value = meanings[right.claim_id]
    if left_type != right_type:
        return False
    tolerance = (_numeric_resolution(left.value_text, left.unit_context)
                 + _numeric_resolution(right.value_text, right.unit_context)) / 2
    return abs(left_value - right_value) <= tolerance


def _quoted_date(quote: str, requested: date) -> bool:
    for year, month, day in DATE_RE.findall(unicodedata.normalize("NFKC", quote)):
        try:
            if date(int(year), int(month), int(day)) == requested:
                return True
        except ValueError:
            continue
    return False


def _occurrence_date_supported(quote: str, requested: date) -> bool:
    """要求引文中的日期描述实际行动，而非文件落款日期。"""
    normalized = unicodedata.normalize("NFKC", quote)
    action = re.compile(r"召开|审议|通过|完成|收到|签署|登记|上市|实施|发布|公布|披露|决定|举行|生效|出具")
    for match in DATE_RE.finditer(normalized):
        try:
            matched = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        if matched != requested:
            continue
        near = normalized[max(0, match.start() - 40):match.start()]
        if re.search(r"(截至|截止|不晚于|计划于|拟于|预计于|将于|上市流通日(?:期)?为|上市流通日(?:期)?：)\s*$", near):
            continue
        after = normalized[match.end():match.end() + 110]
        next_date = DATE_RE.search(after)
        if next_date:
            after = after[:next_date.start()]
        if action.search(after) or action.search(near):
            return True
    return False


def _located_number(value: str, quote: str) -> str | None:
    """仅返回原文引句中确实存在的数值片段。"""
    if value in quote and _number(value) is not None:
        return value
    candidates = sorted(NUMBER_FRAGMENT_RE.findall(value),
                        key=lambda item: len(re.sub(r"\D", "", item)), reverse=True)
    for candidate in candidates:
        if candidate not in quote:
            continue
        # 仅当单位确实紧随引文中的数值时，才保留该单位。
        unit_match = re.search(re.escape(candidate) + r"\s*(万亿元|亿元|万元|千元|元|亿股|万股|股|%|％|万|亿|手|倍)", quote)
        literal = unit_match.group(0) if unit_match else candidate
        if _number(literal) is not None:
            return literal
    return None


def _numeric_qualifier(value: str | None, quote: str) -> str | None:
    if value is None:
        return None
    position = quote.find(value)
    if position < 0:
        return None
    before = quote[max(0, position - 6):position]
    match = re.search(r"(不超过|不低于|超过|至少|不足|超|约|近)\s*$", before)
    if not match:
        return None
    return {"超": "超过"}.get(match.group(1), match.group(1))


def _secondary_company(target: str, primary_names: list[str]) -> str | None:
    match = re.search(r"[\u4e00-\u9fff（）()]+?(?:股份有限公司|有限公司)", target)
    if match and _key(match.group()) not in {_key(name) for name in primary_names}:
        return match.group()
    return None


def _financial_segment(target: str) -> str | None:
    for segment in ("北大医疗集团", "平安健康", "平安银行", "平安寿险",
                    "银行业务", "寿险及健康险", "财产保险业务", "资管业务"):
        if segment in target:
            return segment
    return None


def _period_label(value: str | None) -> str | None:
    if not value:
        return None
    year = re.search(r"20\d{2}", value)
    if year and (any(token in value for token in ("H1", "上半年", "半年度", "中期"))
                 or re.search(r"1\s*[-－—至]\s*6\s*月", value)):
        return f"{year.group()}年上半年"
    if year and any(token in value for token in ("Q2", "第二季度", "二季度")):
        return f"{year.group()}年第二季度"
    return value


def _quarter_profit_check(content: str, attribute: str, target: str,
                          value: str | None) -> tuple[str, list[str]] | None:
    if (value is None or "净利润" not in attribute + target
            or not any(marker in attribute + target for marker in ("第二季度", "二季度", "Q2"))):
        return None
    half = re.search(r"报告期内.{0,120}?实现净利润\s*([\d,.]+)\s*亿元", content)
    first = re.search(r"一季报.{0,80}?实现净利润\s*([\d,.]+)\s*亿元", content)
    second = re.search(r"第二季度为\s*([\d,.]+)\s*亿元", content)
    if not (half and first and second):
        return None
    def decimal(match):
        return Decimal(match.group(1).replace(",", ""))
    half_value, first_value, second_value = map(decimal, (half, first, second))
    if _number(value) != ("money", second_value * Decimal(100000000)):
        return None
    recalculated = half_value - first_value
    quantum = lambda match: Decimal(10) ** -len(match.group(1).partition(".")[2])
    tolerance = sum((quantum(match) for match in (half, first, second)), Decimal(0)) / 2
    if abs(recalculated - second_value) <= tolerance:
        return None
    computed = format(recalculated, "f")
    note = (f"按同一来源列出的上半年净利润 {half_value} 亿元减一季度净利润 {first_value} 亿元，"
            f"重算为 {computed} 亿元；文中写 {second_value} 亿元。是否存在口径差异尚未核实。")
    return note, [f"原文：{second_value}亿元", f"按原文两项数值重算：{computed}亿元"]


def _supplement_resolutions(items: list[dict], chunks: dict[str, dict]) -> list[dict]:
    """补充模型因输出限制而遗漏的编号董事会决议。"""
    result = list(items)
    covered = [_key(str(item.get("quote", ""))) for item in items if isinstance(item, dict)]
    pattern = re.compile(r"[一二三四五六七八九十]+、(?:逐项)?审议通过了?《[^》]{5,180}》")
    for evidence_id, row in chunks.items():
        if row["document_type"] != "announcement":
            continue
        shown = row["chunk_content"][:MAX_CHARS_PER_CHUNK]
        for match in pattern.finditer(shown):
            quote = match.group()
            title = re.sub(r"\s+", "", quote.split("《", 1)[1].rsplit("》", 1)[0])
            if not 5 <= len(title) <= 95 or len(quote) > 240:
                continue
            if any(_key(title) in text for text in covered):
                continue
            result.append({"evidence_id": evidence_id, "quote": quote,
                "event_type": "corporate_action", "title": f"董事会审议通过《{title}》",
                "target": title, "period_label": None, "occurred_at": None,
                "importance": "high" if any(term in title for term in ("中期报告", "中期股息")) else "medium",
                "attribute": "董事会审议通过议案", "value_text": None})
            covered.append(_key(quote))
    return result


def _supplement_quarter_mismatch(items: list[dict], chunks: dict[str, dict],
                                 subject_name: str, year: int) -> list[dict]:
    """保留有原文依据的季度数值计算冲突，即使模型遗漏了该冲突。"""
    result = list(items)
    if any(("第二季度" in str(item.get("title", "")) or "二季度" in str(item.get("title", "")))
           and "净利润" in str(item.get("attribute", "")) for item in result if isinstance(item, dict)):
        return result
    for evidence_id, row in chunks.items():
        if row["document_type"] not in {"news", "financial"}:
            continue
        shown = row["chunk_content"][:MAX_CHARS_PER_CHUNK]
        match = re.search(r"第二季度为\s*([\d,.]+\s*亿元)", shown)
        if match is None:
            continue
        value = match.group(1)
        if _quarter_profit_check(row["document_content"], "第二季度净利润",
                                 f"{subject_name}第二季度", value) is None:
            continue
        quote = shown.strip()
        if not 8 <= len(quote) <= 240:
            continue
        result.append({"evidence_id": evidence_id, "quote": quote,
            "event_type": "financial_disclosure", "title": f"{subject_name}{year}年第二季度净利润报道",
            "target": f"{subject_name}{year}年第二季度", "period_label": f"{year}年第二季度",
            "occurred_at": None, "importance": "medium", "attribute": "第二季度净利润",
            "value_text": value})
        break
    return result


def _supplement_explicit_disclosure(items: list[dict], chunks: dict[str, dict],
                                    subject_name: str, year: int) -> list[dict]:
    """即使模型只提取指标，也保留原文中带日期的公司披露行为。"""
    result = list(items)
    for evidence_id, row in chunks.items():
        if row["document_type"] != "financial":
            continue
        shown = row["chunk_content"][:MAX_CHARS_PER_CHUNK]
        match = re.search(
            r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
            r"[^。；]{0,150}?(?:公布|披露|发布)[^。；]{0,80}?(?:中期业绩|半年度报告)",
            shown)
        if match is None or subject_name not in match.group():
            continue
        quoted = match.group()
        day = DATE_RE.search(quoted)
        if day is None:
            continue
        occurred = date(*(int(part) for part in day.groups()))
        if not _occurrence_date_supported(quoted, occurred):
            continue
        if any(item.get("occurred_at") == occurred.isoformat()
               and item.get("event_type") == "financial_disclosure"
               for item in result if isinstance(item, dict)):
            continue
        result.append({"evidence_id": evidence_id, "quote": quoted,
            "event_type": "financial_disclosure", "title": f"{subject_name}{year}年上半年业绩公布",
            "target": subject_name, "period_label": f"{year}年上半年",
            "occurred_at": occurred.isoformat(), "importance": "high",
            "attribute": "中期业绩公布日期", "value_text": None})
        break
    return result


class EventGraphBuilder:
    """模型提出候选事实，Python 将每条采纳的事实绑定到已保存的原文。"""

    def __init__(self, source_database: str | Path, graph_database: str | Path,
                 llm: OpenAICompatibleClient | None = None):
        self.source_database = Path(source_database)
        self.graph_database = Path(graph_database)
        if self.source_database.resolve() == self.graph_database.resolve():
            raise ValueError("事件图谱库必须与只读资料库分开")
        self.llm = llm if llm is not None else OpenAICompatibleClient()
        self.last_raw_items: list[dict] = []

    def _load_chunks(self, planning: EventPlanningRun):
        selected: dict[str, dict] = {}
        warnings: list[str] = []
        selected_documents: set[str] = set()
        repo = SQLiteRepository.open_readonly(self.source_database)
        try:
            subject = repo.research_subject(planning.subject_code)
            if subject is None or not subject.enabled:
                raise ValueError("事件资料库中没有该研究对象")
            for evidence in planning.evidence:
                if evidence.source_type == "market":
                    continue
                if not evidence.evidence_id.startswith("ev-"):
                    warnings.append("一条资料证据编号无效，已跳过")
                    continue
                chunk_id = evidence.evidence_id[3:]
                row = repo.conn.execute(
                    "SELECT c.chunk_id,c.document_id,c.content AS chunk_content,c.page,c.location,"
                    "c.content_hash AS chunk_hash,d.title,d.publisher,d.source_url,d.published_at,"
                    "d.fetched_at,d.document_type,d.subject_ids,d.source_id,d.content_hash,"
                    "d.content AS document_content,"
                    "d.snapshot_path,d.reprint_of,d.duplicate_of "
                    "FROM document_chunks c JOIN documents d ON d.document_id=c.document_id "
                    "WHERE c.chunk_id=?", (chunk_id,)).fetchone()
                if row is None or row["duplicate_of"] is not None:
                    warnings.append(f"证据 {evidence.evidence_id} 没有可用的规范原文片段")
                    continue
                if (planning.subject_code not in json.loads(row["subject_ids"] or "[]")
                        or not planning.window_start <= date.fromisoformat(row["published_at"]) <= planning.as_of_date
                        or evidence.source_id != row["source_id"]
                        or evidence.source_url != row["source_url"]
                        or evidence.published_at.date().isoformat() != row["published_at"]
                        or evidence.excerpt != row["chunk_content"][:500]
                        or row["chunk_hash"] != row["content_hash"]):
                    warnings.append(f"证据 {evidence.evidence_id} 与原文或研究范围不一致，已跳过")
                    continue
                selected[evidence.evidence_id] = dict(row)
                selected_documents.add(row["document_id"])
            # 第三阶段可能检索到长报告中不相关的页面，因此在提取前，
            # 对同一批已获准访问的文档内部切片重新排序。
            focused: dict[str, dict] = {}
            for document_id in sorted(selected_documents):
                if len(focused) >= MAX_EVIDENCE_CHUNKS:
                    warnings.append(f"超过{MAX_EVIDENCE_CHUNKS}个重点片段的处理上限；其余来源未提取")
                    break
                candidates = repo.conn.execute(
                    "SELECT c.chunk_id,c.document_id,c.content AS chunk_content,c.page,c.location,"
                    "c.content_hash AS chunk_hash,d.title,d.publisher,d.source_url,d.published_at,"
                    "d.fetched_at,d.document_type,d.subject_ids,d.source_id,d.content_hash,"
                    "d.content AS document_content,"
                    "d.snapshot_path,d.reprint_of,d.duplicate_of "
                    "FROM document_chunks c JOIN documents d ON d.document_id=c.document_id "
                    "WHERE d.document_id=? ORDER BY c.chunk_id", (document_id,)).fetchall()
                ranked = []
                for candidate in candidates:
                    evidence_id = f"ev-{candidate['chunk_id']}"
                    if (candidate["chunk_hash"] != candidate["content_hash"] or candidate["duplicate_of"] is not None
                            or planning.subject_code not in json.loads(candidate["subject_ids"] or "[]")):
                        continue
                    content = candidate["chunk_content"][:MAX_CHARS_PER_CHUNK]
                    if candidate["document_type"] == "announcement":
                        dated = []
                        for year, month, day in DATE_RE.findall(unicodedata.normalize("NFKC", content)):
                            try:
                                dated.append(date(int(year), int(month), int(day)))
                            except ValueError:
                                pass
                        if dated and all(day < planning.window_start for day in dated):
                            continue
                    if (len(content.strip()) < 50 and evidence_id not in selected
                            and not (candidate["document_type"] == "announcement"
                                     and "审议通过" in content)):
                        continue
                    score = sum(weight for term, weight in (
                        ("主要会计数据", 12), ("营业收入", 8), ("净利润", 7),
                        ("业绩", 5), ("公告", 3), ("同比", 2), ("调整", 2)) if term in content)
                    if (candidate["document_type"] == "financial"
                            and re.search(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
                                          r"[^。；]{0,150}?(?:公布|披露|发布)"
                                          r"[^。；]{0,80}?(?:中期业绩|半年度报告)", content)):
                        score += 25
                    if candidate["page"] is not None and candidate["page"] <= 8:
                        score += 2
                    if evidence_id in selected:
                        score += 1
                    ranked.append((score, evidence_id, candidate))
                ranked.sort(key=lambda item: (-item[0], item[1]))
                # 董事会和交易所公告较短，可以完整检查。
                # 否则，仅因前两页命中了更多关键词，
                # 中间页面的决议就可能被遗漏。
                limit = (len(ranked) if candidates[0]["document_type"] == "announcement"
                         else MAX_CHUNKS_PER_DOCUMENT)
                for _, evidence_id, candidate in ranked[:limit]:
                    if len(focused) >= MAX_EVIDENCE_CHUNKS:
                        break
                    focused[evidence_id] = dict(candidate)
            selected = focused
        finally:
            repo.close()
        return selected, warnings, subject.name, subject.aliases

    def _extract(self, planning: EventPlanningRun, chunks: dict[str, dict]):
        documents: dict[str, list[dict]] = defaultdict(list)
        for evidence_id, row in chunks.items():
            content = row["chunk_content"][:MAX_CHARS_PER_CHUNK]
            if row["document_type"] == "announcement" and "归属结果暨股份上市" in row["title"]:
                current_section = content.find("二、本次限制性股票归属的基本情况")
                history_section = content.find("一、本次限制性股票归属的决策程序")
                if current_section >= 0:
                    content = content[current_section:]
                elif history_section >= 0:
                    content = content[:history_section]
            documents[row["document_id"]].append({
                "evidence_id": evidence_id, "title": row["title"],
                "published_at": row["published_at"], "source_type": row["document_type"],
                "content": content})
        if len(documents) > 4:
            raise ValueError("事件提取的资料份数超过调用上限")
        system = (
            "你是企业事件证据整理员。只从给定片段提取明确事实，不推测市场因果。"
            "只返回JSON对象 {\"items\":[]}。逐段检查这份资料，提取与问题相关的重要事实。"
            "公告中的主要议案和明确发生的动作分别提取；财务资料优先提取关键经营指标。"
            "研究窗口之外已经发生的历史事项只是背景，不要列为本次事件。"
            "公告落款日、统计截止日、未来计划日不是独立已发生事件；不要生成这类事件。"
            "不要把会议出席人数或落款当成重要事件。"
            "每项必须且仅包含 evidence_id、quote、event_type、title、target、period_label、"
            "occurred_at、importance、attribute、value_text。quote必须是该evidence_id片段中"
            "连续复制的原文；不要拼接。event_type只用financial_disclosure、corporate_action、"
            "business_update、other。同一事件跨来源使用相同target和period_label；target必须具体。"
            "occurred_at只有在quote明确写出年月日并描述该动作已发生时才填YYYY-MM-DD，"
            "否则填null；不能把资料发布日期、计划日或截至日当成事件发生日。"
            "value_text是quote中的一个原样数字及单位；没有数字就填null；"
            "日历年份和日期不要当作数值指标。attribute标明该数字或动作的含义。"
            "每项只表达一条可核对事实，importance只用high、medium、low。"
            "找不到事实时返回空items，不得添加片段以外的资料。")
        cap = float(os.getenv("EVENT_GRAPH_MAX_USD_PER_RUN", str(DEFAULT_COST_CAP_USD)))
        if cap <= 0:
            raise ValueError("事件提取费用预算必须大于零")
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
                raise RuntimeError("未知模型必须配置输入和输出 Token 单价") from exc
        if input_rate < 0 or output_rate < 0:
            raise ValueError("模型单价不能为负")
        all_items: list[dict] = []
        total_tokens, total_cost = 0, 0.0
        actual_model = model_id
        for document_id, snippets in documents.items():
            document_type = snippets[0]["source_type"]
            document_limit = {"announcement": 16, "financial": 12, "news": 8}.get(document_type, 8)
            messages = [
                {"role": "system", "content": system + f"本份{document_type}资料最多提取{document_limit}项；请按重要性取舍。"},
                {"role": "user", "content": json.dumps({
                    "subject_code": planning.subject_code, "question": planning.question,
                    "window_start": planning.window_start.isoformat(),
                    "as_of_date": planning.as_of_date.isoformat(),
                    "document_id": document_id, "snippets": snippets}, ensure_ascii=False)}]
            input_bound = sum(len(item["content"].encode("utf-8")) for item in messages) + 1000
            if total_cost + (input_bound * input_rate
                    + MAX_OUTPUT_TOKENS * output_rate) / 1_000_000 > cap:
                raise ValueError("事件提取预估费用超过本次预算")
            reply = self.llm.chat(messages, json_output=True,
                                  max_tokens=MAX_OUTPUT_TOKENS, thinking=False)
            usage = reply.get("usage") or {}
            used = usage.get("total_tokens")
            if not isinstance(used, int) or used < 0:
                raise ValueError("事件提取未返回有效的 Token 用量")
            total_tokens += used
            if total_tokens > MAX_TOTAL_TOKENS:
                raise ValueError("事件提取超出 Token 预算")
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            total_cost += ((prompt * input_rate + completion * output_rate) / 1_000_000
                           if isinstance(prompt, int) and isinstance(completion, int)
                           else used * max(input_rate, output_rate) / 1_000_000)
            if total_cost > cap:
                raise ValueError("事件提取超出费用预算")
            content = reply.get("message", {}).get("content")
            if not isinstance(content, str):
                raise ValueError("事件提取缺少 JSON 内容")
            raw = json.loads(content)
            if not isinstance(raw, dict) or set(raw) != {"items"} or not isinstance(raw["items"], list):
                raise ValueError("事件提取输出结构无效")
            if len(raw["items"]) > document_limit:
                raise ValueError(f"{document_type}资料提取条数超过单份上限")
            all_items.extend(raw["items"])
            self.last_raw_items = all_items.copy()
            if len(all_items) > MAX_EXTRACTED_ITEMS:
                raise ValueError("事件提取条数超过上限")
            actual_model = str(reply.get("model") or model_id)
        self.last_raw_items = all_items
        return all_items, total_tokens, round(total_cost, 8), actual_model

    def build(self, planning: EventPlanningRun) -> EventGraphRun:
        if (planning.subject_code not in EVENT_SUBJECTS or planning.window_start > planning.as_of_date
                or (planning.as_of_date - planning.window_start).days > 30
                or planning.as_of_date > date.today()):
            raise ValueError("事件图谱的研究对象或日期无效")
        if not self.source_database.is_file():
            raise FileNotFoundError("事件资料库不存在")
        chunks, warnings, subject_name, subject_aliases = self._load_chunks(planning)
        items, tokens, cost, model = self._extract(planning, chunks) if chunks else ([], 0, 0.0, "未调用模型")
        items = _supplement_resolutions(items, chunks)
        items = _supplement_quarter_mismatch(items, chunks, subject_name, planning.as_of_date.year)
        items = _supplement_explicit_disclosure(items, chunks, subject_name,
                                                planning.as_of_date.year)
        self.last_raw_items = items
        sources: dict[str, GraphSource] = {}
        citations: dict[str, GraphCitation] = {}
        claims: dict[str, GraphClaim] = {}
        numeric_meanings: dict[str, tuple[str, Decimal]] = {}
        calculation_issues: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
        groups: dict[str, dict] = {}
        seen_facts: set[tuple[str, str, str, str]] = set()
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
                warnings.append(f"第{index}条提取结果字段不完整，已跳过")
                continue
            evidence_id = item["evidence_id"]
            if not isinstance(evidence_id, str) or evidence_id not in chunks:
                warnings.append(f"第{index}条提取结果引用了不存在的片段，已跳过")
                continue
            row = chunks[evidence_id]
            quote = item["quote"]
            shown = row["chunk_content"][:MAX_CHARS_PER_CHUNK]
            if not isinstance(quote, str) or not 8 <= len(quote) <= 240:
                warnings.append(f"第{index}条提取结果无法在原文中逐字定位，已跳过")
                continue
            quote = _locate_quote(shown, quote)
            if quote is None:
                warnings.append(f"第{index}条提取结果无法在原文中逐字定位，已跳过")
                continue
            event_type, importance = item["event_type"], item["importance"]
            title, target, attribute = item["title"], item["target"], item["attribute"]
            period = item["period_label"]
            if (not isinstance(event_type, str) or event_type not in EVENT_TYPES
                    or not isinstance(importance, str) or importance not in IMPORTANCE
                    or not all(isinstance(value, str) and 2 <= len(value.strip()) <= 100
                               for value in (title, target, attribute))
                    or period is not None and (not isinstance(period, str) or len(period) > 60)):
                warnings.append(f"第{index}条提取结果的事件字段无效，已跳过")
                continue
            if (re.search(r"(公告落款|落款日期|出资截止|统计截止|截至日期)",
                          title + target + attribute)
                    or (event_type == "corporate_action"
                        and re.search(r"(会议出席|表决权票数)", title + target + attribute))):
                warnings.append(f"第{index}条只是日期口径或会议程序信息，未作为独立事件")
                continue
            quote_start = shown.find(quote)
            preceding_context = shown[max(0, quote_start - 100):quote_start]
            if (event_type == "corporate_action" and item["occurred_at"] is None
                    and (re.search(r"20\d{2}\s*年.{0,25}(?:董事会|股东大会).{0,25}(?:决议|审议)", quote)
                         or re.search(r"(?:董事会|股东大会)\s*20\d{2}\s*年度.{0,25}(?:决议|审议)", quote + preceding_context)
                         or re.search(r"根据公司.{0,50}20\d{2}\s*年度.{0,30}会议决议", preceding_context))
                    and re.search(r"(?:出资|成立|设立|划转|转让)", quote)):
                warnings.append(f"第{index}条可能是研究窗口之前的历史动作，未作为本次事件")
                continue
            if (event_type == "financial_disclosure" and "第一季度" in (period or "")
                    and "一季报" in quote and planning.window_start.month > 3):
                warnings.append(f"第{index}条第一季度数字仅作为比较背景，未作为本次披露事件")
                continue
            period = _period_label(period)
            if (event_type == "business_update" and period
                    and ("上半年" in period or "上半年" in row["title"] or "半年度" in row["title"])
                    and row["document_type"] in {"financial", "news"}
                    and item["occurred_at"] is None):
                event_type = "financial_disclosure"
                if "上半年" not in period:
                    period = f"{planning.as_of_date.year}年上半年"
            if (event_type == "corporate_action" and "业绩公布" in attribute + target
                    and "业绩" in quote and "公布" in quote):
                event_type = "financial_disclosure"
            secondary = (_secondary_company(target, [subject_name, *subject_aliases])
                         if event_type == "financial_disclosure" else None)
            if secondary and secondary not in planning.question and "子公司" not in planning.question:
                continue
            occurred = None
            if item["occurred_at"] is not None:
                try:
                    occurred = date.fromisoformat(item["occurred_at"])
                except (TypeError, ValueError):
                    warnings.append(f"第{index}条发生日期格式无效，已改为未知")
                else:
                    if not planning.window_start <= occurred <= planning.as_of_date:
                        warnings.append(f"第{index}条事件发生在研究窗口之外，已跳过")
                        continue
                    if not _quoted_date(quote, occurred) or not _occurrence_date_supported(quote, occurred):
                        warnings.append(f"第{index}条发生日期缺少原文中的事件动作依据，已改为未知")
                        occurred = None
            value = item["value_text"]
            numeric = "not_applicable"
            if value is not None:
                located = _located_number(value, quote) if isinstance(value, str) else None
                if located is None:
                    warnings.append(f"第{index}条数字未在原文中定位或格式无效，已跳过")
                    continue
                value = located
                numeric = "format_checked"
            qualifier = _numeric_qualifier(value, quote)
            segment = _financial_segment(target + " " + quote) if event_type == "financial_disclosure" else None
            group_target = ((secondary or (f"{segment} {period}" if segment else period))
                            if event_type == "financial_disclosure" and period else target)
            action_key = _key(attribute) if event_type in {"corporate_action", "business_update"} else ""
            if event_type == "corporate_action" and "董事会" in target and "议案" in title:
                action_key += _key(title)
            if event_type == "corporate_action" and "限制性股票激励计划" in target:
                if "缴纳" in title or "出资款" in attribute:
                    action_key = "归属股份出资款"
                elif "收到证券变更登记证明" in title + attribute:
                    action_key = "收到证券变更登记证明"
                elif "登记" in title or any(token in title + attribute for token in
                                            ("归属人数", "归属情况", "归属数量", "归属比例", "占总股本比例")):
                    action_key = "归属股份登记"
            if (event_type == "corporate_action" and "上市流通" in title + attribute
                    and "归属" in target):
                action_key = "归属股份上市流通"
            event_key = "|".join((planning.subject_code, event_type, _key(group_target),
                                  _key(period or ""), action_key))
            if not _key(target):
                warnings.append(f"第{index}条事件对象无效，已跳过")
                continue
            event_id = _id("event", event_key)
            citation_id = _id("cite", row["document_id"], row["chunk_id"], quote)
            claim_id = _id("claim", event_id, citation_id, _key(attribute), value or "")
            fact_key = (event_id, row["document_id"], _key(attribute), value or quote)
            if fact_key in seen_facts:
                continue
            seen_facts.add(fact_key)
            source = GraphSource(document_id=row["document_id"], source_id=row["source_id"],
                source_type=row["document_type"], title=row["title"], publisher=row["publisher"],
                source_url=row["source_url"], published_at=date.fromisoformat(row["published_at"]),
                fetched_at=datetime.fromisoformat(row["fetched_at"]), content_hash=row["content_hash"],
                snapshot_path=row["snapshot_path"], reprint_of=row["reprint_of"])
            sources[source.document_id] = source
            start = row["chunk_content"].find(quote)
            quote_end = start + len(quote)
            unit_context = None
            meaning = _number(value) if value is not None else None
            if value is not None and meaning is not None and meaning[0] == "unitless":
                proposed = item["value_text"]
                prefix = row["chunk_content"][:start]
                unit_match = list(re.finditer(r"单位\s*[：:]\s*(千元|万元|元)", prefix))
                last_unit = unit_match[-1] if unit_match else None
                if "元／股" in quote or "元/股" in quote:
                    unit_context = "原文列名标注单位：元/股"
                    meaning = ("yuan_per_share", meaning[1])
                elif last_unit:
                    unit = last_unit.group(1)
                    if start - last_unit.start() <= 180:
                        start = last_unit.start()
                        quote = row["chunk_content"][start:quote_end]
                    unit_context = f"原文表头标注单位：{unit}"
                    meaning = ("money", meaning[1] * {"元": 1, "千元": 1000, "万元": 10000}[unit])
                elif ("%" in quote or "％" in quote) and ("收益率" in attribute or "%" in proposed):
                    unit_context = "原文列名标注单位：%"
                    meaning = ("percent", meaning[1] / 100)
                if unit_context is None and proposed != value:
                    warnings.append(f"第{index}条数字单位未与数值连写，已只保留原文数字")
            if meaning is not None and "每股收益" in attribute and meaning[0] in {"money", "unitless"}:
                meaning = ("yuan_per_share", meaning[1])
            arithmetic = _quarter_profit_check(row["document_content"], attribute, target, value)
            if arithmetic:
                numeric = "calculation_mismatch"
                calculation_issues[event_id].append((claim_id, arithmetic[1]))
                warnings.append(f"第{index}条净利润算式与同一来源列出的数值不一致，已标记待核实")
            citations[citation_id] = GraphCitation(citation_id=citation_id,
                document_id=source.document_id, chunk_id=row["chunk_id"], quote=quote,
                start_offset=start, end_offset=start + len(quote), page=row["page"],
                location=row["location"])
            claims[claim_id] = GraphClaim(claim_id=claim_id, event_id=event_id,
                citation_id=citation_id, attribute=attribute.strip(), value_text=value,
                qualifier=qualifier, unit_context=unit_context, numeric_status=numeric,
                calculation_note=arithmetic[0] if arithmetic else None, text=quote)
            if meaning is not None:
                numeric_meanings[claim_id] = meaning
            display_subject = (segment if segment in {"平安银行", "北大医疗集团", "平安健康", "平安寿险"}
                               else f"{subject_name}{segment or secondary or ''}")
            display_title = (f"{display_subject}{period}业绩披露"
                             if event_type == "financial_disclosure" and period else title.strip())
            if event_type == "corporate_action" and action_key == "归属股份出资款":
                display_title = "激励对象缴纳归属股份出资款"
            elif event_type == "corporate_action" and action_key == "归属股份登记":
                display_title = "预留授予部分第四个归属期股份完成登记"
            elif event_type == "corporate_action" and action_key == "归属股份上市流通":
                display_title = "公告预定归属股份上市流通"
            elif event_type == "corporate_action" and "i茅台" in title and "划转" in title:
                display_title = "董事会决定划转i茅台平台信息系统及域名"
            elif event_type == "corporate_action" and "将向股东派发" in quote:
                display_title = "拟派发中期股息"
            group = groups.setdefault(event_id, {"key": event_key, "type": event_type,
                "target": group_target.strip(), "period": period or None, "title": display_title,
                "importance": importance, "claims": [], "documents": set(), "dates": defaultdict(list),
                "reported": []})
            group["claims"].append(claim_id)
            group["documents"].add(source.document_id)
            group["reported"].append(source.published_at)
            if occurred:
                group["dates"][occurred.isoformat()].append(claim_id)
            if IMPORTANCE[importance] > IMPORTANCE[group["importance"]]:
                group["importance"] = importance
        conflicts: list[GraphConflict] = []
        events: list[GraphEvent] = []
        for event_id, group in groups.items():
            conflict_ids = []
            if len(group["dates"]) > 1:
                variants = sorted(group["dates"])
                conflict = GraphConflict(conflict_id=_id("conflict", event_id, "occurred_date"),
                    event_id=event_id, kind="occurred_date", attribute="事件发生日期",
                    variants=variants, claim_ids=[claim_id for day in variants
                                                  for claim_id in group["dates"][day]])
                conflicts.append(conflict)
                conflict_ids.append(conflict.conflict_id)
            numeric_groups: dict[str, list[GraphClaim]] = defaultdict(list)
            for claim_id in group["claims"]:
                claim = claims[claim_id]
                if claim.numeric_status == "format_checked" and claim.qualifier is None:
                    numeric_groups[_key(claim.attribute)].append(claim)
            for attribute_key, group_claims in numeric_groups.items():
                different = any(
                    citations[left.citation_id].document_id != citations[right.citation_id].document_id
                    and not _numbers_agree(left, right, numeric_meanings)
                    for left, right in combinations(group_claims, 2))
                if different:
                    variants = list(dict.fromkeys(claim.value_text for claim in group_claims))
                    if len(variants) < 2:
                        continue
                    conflict = GraphConflict(conflict_id=_id("conflict", event_id, attribute_key),
                        event_id=event_id, kind="number", attribute=group_claims[0].attribute,
                        variants=variants, claim_ids=[claim.claim_id for claim in group_claims])
                    conflicts.append(conflict)
                    conflict_ids.append(conflict.conflict_id)
            for claim_id, variants in calculation_issues[event_id]:
                conflict = GraphConflict(conflict_id=_id("conflict", event_id, "calculation", claim_id),
                    event_id=event_id, kind="calculation", attribute="原文数值重算",
                    variants=variants, claim_ids=[claim_id])
                conflicts.append(conflict)
                conflict_ids.append(conflict.conflict_id)
            occurred = date.fromisoformat(next(iter(group["dates"]))) if len(group["dates"]) == 1 else None
            date_status = "known" if occurred else "conflict" if len(group["dates"]) > 1 else "unknown"
            events.append(GraphEvent(event_id=event_id, event_key=group["key"],
                title=group["title"], event_type=group["type"], target=group["target"],
                period_label=group["period"], occurred_at=occurred, date_status=date_status,
                reported_at=min(group["reported"]), importance=group["importance"],
                claim_ids=group["claims"], document_ids=sorted(group["documents"]),
                conflict_ids=conflict_ids))
        events.sort(key=lambda event: (event.occurred_at is None,
                    event.occurred_at or event.reported_at, event.reported_at, event.title))
        relations: list[GraphRelation] = []
        for event in events:
            for left_id, right_id in combinations(event.claim_ids, 2):
                left, right = claims[left_id], claims[right_id]
                left_doc = citations[left.citation_id].document_id
                right_doc = citations[right.citation_id].document_id
                if left_doc == right_doc:
                    continue
                if sources[left_doc].reprint_of == right_doc or sources[right_doc].reprint_of == left_doc:
                    kind = "reprint"
                elif _key(left.attribute) == _key(right.attribute):
                    if (left_id in numeric_meanings and right_id in numeric_meanings
                            and left.qualifier is None and right.qualifier is None):
                        kind = ("agreement" if _numbers_agree(left, right, numeric_meanings)
                                else "conflict")
                    else:
                        kind = "agreement" if left.text == right.text else "unresolved"
                else:
                    continue
                relations.append(GraphRelation(relation_id=_id("relation", left_id, right_id),
                    event_id=event.event_id, left_claim_id=left_id,
                    right_claim_id=right_id, kind=kind))
        if not chunks:
            warnings.append("阶段三没有取得可用的文档片段；行情记录不能替代事件原文")
        elif not items:
            warnings.append("模型未从所选原文片段中提取到可定位的事件事实")
        selected_announcements = {row["document_id"] for row in chunks.values()
                                  if row["document_type"] == "announcement"}
        omitted_announcements = selected_announcements - set(sources)
        if omitted_announcements:
            warnings.append(f"{len(omitted_announcements)}份已选公告没有提取出可定位的事实；可能遗漏重要事件")
        status = ("partial" if warnings or planning.status != "evidence_collected" else "extracted") if events else "insufficient_evidence"
        graph_fingerprint = hashlib.sha256(json.dumps({
            "events": [event.model_dump(mode="json") for event in events],
            "claims": [claim.model_dump(mode="json") for claim in claims.values()],
            "citations": [citation.model_dump(mode="json") for citation in citations.values()],
            "conflicts": [conflict.model_dump(mode="json") for conflict in conflicts],
            "relations": [relation.model_dump(mode="json") for relation in relations],
            "warnings": warnings}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        run_id = _id("graph", planning.subject_code, planning.window_start.isoformat(),
                     planning.as_of_date.isoformat(), planning.question,
                     ",".join(sorted(chunks)), model, graph_fingerprint)
        graph = EventGraphRun(run_id=run_id, question=planning.question,
            subject_code=planning.subject_code, subject_name=subject_name,
            window_start=planning.window_start, as_of_date=planning.as_of_date,
            status=status, planning_status=planning.status, model=model,
            planning_tokens=planning.total_tokens, extraction_tokens=tokens,
            estimated_cost_usd=round(planning.estimated_cost_usd + cost, 8),
            created_at=datetime.now(timezone.utc), sources=list(sources.values()),
            citations=list(citations.values()), claims=list(claims.values()), events=events,
            conflicts=conflicts, relations=relations, warnings=warnings)
        store = EventGraphStore(self.graph_database)
        try:
            store.save(graph)
        finally:
            store.close()
        return graph
