"""
Thin OpenAI-compatible LLM client, wired to Gemini's OpenAI-compatible
endpoint by default.

Every caller in this codebase already works with no LLM configured -- the
Stock Review agents are deterministic first, LLM-enhanced second. This module
degrades to ``None`` (never raises) whenever the API key is missing, the
``openai`` package isn't installed, or the call fails, so a missing/invalid
GEMINI_API_KEY never breaks a stock review; it just skips the narrative
layer.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-2.5-flash"


def _get_config() -> Any:
    try:
        from config import get_config

        return get_config()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[llm_client] config unavailable: %s", exc)
        return None


def is_configured() -> bool:
    """True if a Gemini API key is set, regardless of whether it's valid."""
    cfg = _get_config()
    return bool(getattr(cfg, "GEMINI_API_KEY", None)) if cfg else False


def get_client() -> Optional[Any]:
    """Build an OpenAI-compatible client pointed at Gemini, or None if unavailable."""
    cfg = _get_config()
    api_key = getattr(cfg, "GEMINI_API_KEY", None) if cfg else None
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        logger.debug("[llm_client] openai package unavailable: %s", exc)
        return None

    base_url = getattr(cfg, "LLM_BASE_URL", DEFAULT_BASE_URL) if cfg else DEFAULT_BASE_URL
    try:
        return OpenAI(api_key=api_key, base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[llm_client] failed to construct client: %s", exc)
        return None


def complete(
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> Optional[str]:
    """Single-turn chat completion. Never raises; returns None if unavailable/failed."""
    client = get_client()
    if client is None:
        return None

    cfg = _get_config()
    model_name = model or (getattr(cfg, "LLM_MODEL", DEFAULT_MODEL) if cfg else DEFAULT_MODEL)
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[llm_client] completion failed: %s", exc)
        return None
