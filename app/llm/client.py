import json
import requests

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL_ID, REQUEST_TIMEOUT

class OpenAICompatibleClient:
    def __init__(self, api_key: str = LLM_API_KEY, base_url: str = LLM_BASE_URL, model: str = LLM_MODEL_ID, enabled: bool = LLM_ENABLED):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.enabled = enabled

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url and self.model)

    def summarize(self, question: str, evidence_payload: dict) -> list[str]:
        if not self.configured:
            return []
        prompt = (
            "你是谨慎的ETF资料摘录助手。只能复制给定文档中的连续原文片段，不得改写、推测因果或新增数字。"
            "每条摘录必须以其文档证据编号结尾，例如[D1]。"
            "只返回JSON对象：{\"source_quotes\":[\"原文片段 [D1]\"]}。\n"
            f"问题：{question}\n证据：{json.dumps(evidence_payload, ensure_ascii=False, default=str)}"
        )
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        values = data.get("source_quotes", [])
        if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
            raise ValueError("LLM返回结构无效")
        return values
