"""
Injectable LLM client abstraction for StockScanner agents.

Design goal: every agent (research, risk, execution, learning) must be
constructible and unit-testable WITHOUT crewai / langchain / anthropic
installed and WITHOUT an API key or network access.

Agents accept an optional ``llm_client``. When none is provided they fall back
to :class:`DeterministicLLM` (rule-based path, empty enrichment). Tests inject
:class:`FakeLLM`. Real runs use :func:`build_llm_client`, which lazily builds a
LangChain/Anthropic-backed client only if the dependency and API key exist,
degrading gracefully to :class:`DeterministicLLM` otherwise.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """Minimal contract an agent needs from a language model."""

    def complete(self, prompt: str, system: str = "") -> str:
        ...


class DeterministicLLM:
    """No-op client. Signals agents to use their rule-based fallback path."""

    name = "deterministic"

    def complete(self, prompt: str, system: str = "") -> str:  # noqa: D401
        return ""


class FakeLLM:
    """Test double. Returns scripted responses and records calls."""

    name = "fake"

    def __init__(self, response: str = "", responses: Optional[List[str]] = None):
        self.response = response
        self.responses = list(responses or [])
        self.calls: List[dict] = []

    def complete(self, prompt: str, system: str = "") -> str:
        self.calls.append({"system": system, "prompt": prompt})
        if self.responses:
            return self.responses.pop(0)
        return self.response


class _LangChainAdapter:
    """Adapts a LangChain chat model to the :class:`LLMClient` contract."""

    name = "langchain-anthropic"

    def __init__(self, llm):
        self._llm = llm

    def complete(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append(("system", system))
        messages.append(("human", prompt))
        try:
            resp = self._llm.invoke(messages)
            return getattr(resp, "content", str(resp))
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("[llm] completion failed: %s", exc)
            return ""


def build_llm_client(
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> LLMClient:
    """Best-effort real client; falls back to DeterministicLLM.

    Never raises: if ``ANTHROPIC_API_KEY`` is missing or ``langchain_anthropic``
    is not installed, returns a :class:`DeterministicLLM` so callers can always
    run deterministically.
    """

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("[llm] no ANTHROPIC_API_KEY; using DeterministicLLM")
        return DeterministicLLM()

    try:
        from langchain_anthropic import ChatAnthropic  # lazy, optional
    except Exception as exc:
        logger.debug("[llm] langchain_anthropic unavailable (%s); DeterministicLLM", exc)
        return DeterministicLLM()

    try:
        llm = ChatAnthropic(
            model=model or "claude-haiku-4-5",
            anthropic_api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _LangChainAdapter(llm)
    except Exception as exc:  # pragma: no cover - construction path
        logger.warning("[llm] ChatAnthropic init failed (%s); DeterministicLLM", exc)
        return DeterministicLLM()
