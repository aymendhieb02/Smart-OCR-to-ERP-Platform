from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.core.config import settings
from app.services.llm_prompt_builder import build_llm_prompt
from app.services.llm_response_parser import LLMResolution, parse_llm_response


@dataclass
class OllamaClient:
    url: str | None = None
    model: str | None = None
    timeout: float | None = None

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model or settings.llm_resolver_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            self.url or settings.llm_resolver_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout or settings.llm_resolver_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body.get("response") or "")


class LLMResolverError(RuntimeError):
    pass


def resolve_with_llm(payload: dict, *, client: OllamaClient | None = None) -> LLMResolution:
    prompt = build_llm_prompt(payload)
    active_client = client or OllamaClient()
    try:
        raw = active_client.generate(prompt)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise LLMResolverError(f"Ollama request failed: {exc}") from exc
    try:
        return parse_llm_response(raw)
    except ValueError as exc:
        raise LLMResolverError(f"Ollama response parse failed: {exc}") from exc
