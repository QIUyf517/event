import json
import re
from typing import Iterable

NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
CITATION_RE = re.compile(r"\[(D\d+)\]")

def verify_generated_inferences(inferences: Iterable[str], evidence_payload: dict, valid_citations: set[str]) -> tuple[list[str], list[str]]:
    allowed_numbers = set(NUMBER_RE.findall(json.dumps(evidence_payload, ensure_ascii=False, default=str)))
    accepted, warnings = [], []
    for text in inferences:
        citations = set(CITATION_RE.findall(text))
        extra_numbers = set(NUMBER_RE.findall(text)).difference(allowed_numbers)
        if not citations or not citations.issubset(valid_citations):
            warnings.append("LLM推测因缺少有效文档引用而被删除")
            continue
        if extra_numbers:
            warnings.append(f"LLM推测因包含证据外数字而被删除: {sorted(extra_numbers)}")
            continue
        accepted.append(text)
    return accepted, warnings
