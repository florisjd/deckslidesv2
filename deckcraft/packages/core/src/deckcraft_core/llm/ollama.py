"""Minimal local LLM gateway (Ollama). Structured output via JSON schema, one repair retry."""
from __future__ import annotations
import json
import httpx
from pydantic import BaseModel, ValidationError


class OllamaLLM:
    def __init__(self, url: str = "http://127.0.0.1:11434", model: str = "gemma3:4b", timeout: float = 120):
        self.url, self.model, self.timeout = url, model, timeout

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=2)
            return any(m["name"].split(":")[0] == self.model.split(":")[0] for m in r.json().get("models", []))
        except Exception:  # noqa: BLE001
            return False

    def generate(self, messages: list[dict], schema: type[BaseModel], temperature: float = 0.4) -> BaseModel:
        msgs = list(messages)
        for attempt in range(2):
            r = httpx.post(f"{self.url}/api/chat", timeout=self.timeout, json={
                "model": self.model, "messages": msgs, "stream": False,
                "format": schema.model_json_schema(), "options": {"temperature": temperature, "num_ctx": 4096}})
            r.raise_for_status()
            raw = r.json()["message"]["content"]
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                if attempt:
                    raise
                msgs += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"That JSON was invalid: {json.dumps(e.errors(include_url=False), default=str)[:800]}. Fix it."}]
        raise RuntimeError("unreachable")
