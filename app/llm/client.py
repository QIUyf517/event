import requests

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL_ID, REQUEST_TIMEOUT

class OpenAICompatibleClient:
    def __init__(self, api_key: str = LLM_API_KEY, base_url: str = LLM_BASE_URL, model: str = LLM_MODEL_ID, enabled: bool = LLM_ENABLED):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.enabled = enabled

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url and self.model)

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             json_output: bool = False, max_tokens: int = 1200,
             require_tool: bool = False, thinking: bool | None = None) -> dict:
        """One bounded Chat Completions request; callers validate every tool argument."""
        if not self.configured:
            raise RuntimeError("尚未配置模型地址、模型名称或 API Key")
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if thinking is not None and "deepseek.com" in self.base_url:
            body["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "required" if require_tool else "auto"
        if json_output:
            body["response_format"] = {"type": "json_object"}
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body, timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter", "aborted", "insufficient_system_resource"}:
            raise ValueError(f"模型回答未完成: {choice['finish_reason']}")
        return {"message": choice["message"], "usage": payload.get("usage", {}),
                "model": payload.get("model", self.model), "finish_reason": choice.get("finish_reason")}
