"""Provider-agnostic chat LLM client.

Selected via the ``LLM_PROVIDER`` env var. Currently supports ``openai``
(default) and ``anthropic``.
"""

from __future__ import annotations

import os
import threading
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMBackend(Protocol):
    model: str

    def complete(self, system: str, user: str, *, temperature: float = 0) -> str: ...


class OpenAIBackend:
    def __init__(self, model: str | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise LLMError("openai package is not installed.") from exc

        self._client = OpenAI(api_key=api_key)
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")

    def complete(self, system: str, user: str, *, temperature: float = 0) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class AnthropicBackend:
    def __init__(self, model: str | None = None):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not configured.")
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as exc:
            raise LLMError("anthropic package is not installed.") from exc

        self._client = Anthropic(api_key=api_key)
        self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    def complete(self, system: str, user: str, *, temperature: float = 0) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate any text blocks in the response
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )


_lock = threading.Lock()
_backend: LLMBackend | None = None


def get_backend() -> LLMBackend:
    global _backend
    if _backend is not None:
        return _backend
    with _lock:
        if _backend is None:
            provider = os.getenv("LLM_PROVIDER", "openai").lower()
            if provider == "openai":
                _backend = OpenAIBackend()
            elif provider == "anthropic":
                _backend = AnthropicBackend()
            else:
                raise LLMError(f"Unsupported LLM_PROVIDER: {provider}")
    return _backend
