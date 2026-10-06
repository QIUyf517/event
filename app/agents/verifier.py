"""只放行能在所引文档中逐字定位的摘录，不推断文档是否解释市场变化。"""

import re
from typing import Iterable


CITATION_RE = re.compile(r"\[(D\d+)\]")
END_CITATION_RE = re.compile(r"\[(D\d+)\]\s*$")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


def verify_generated_source_quotes(
    candidates: Iterable[str], documents: list[dict], valid_citations: set[str]
) -> tuple[list[str], list[str]]:
    """核对编号和连续原文；返回带引用的原文摘录及拒绝原因。"""
    by_citation = {row.get("citation"): row.get("content", "") for row in documents}
    accepted, warnings = [], []
    for text in candidates:
        citations = CITATION_RE.findall(text)
        ending = END_CITATION_RE.search(text)
        if len(citations) != 1 or ending is None or citations[0] not in valid_citations:
            warnings.append("生成内容因缺少唯一且有效的文档引用而被删除")
            continue
        citation = citations[0]
        quote = text[:ending.start()].strip().strip("“”\"'，。；;,.!?！？")
        source = by_citation.get(citation, "")
        if len(_normalized(quote)) < 8 or _normalized(quote) not in _normalized(source):
            warnings.append(f"生成内容在所引原文 [{citation}] 中无法逐字定位，已删除")
            continue
        accepted.append(f"“{quote}” [{citation}]")
    return accepted, warnings


def verify_generated_inferences(inferences: Iterable[str], evidence_payload: dict, valid_citations: set[str]) -> tuple[list[str], list[str]]:
    """兼容旧调用；只返回逐字可定位的文档摘录。"""
    return verify_generated_source_quotes(inferences, evidence_payload.get("documents", []), valid_citations)
