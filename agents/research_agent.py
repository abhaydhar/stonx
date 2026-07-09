"""
Research Agent (RES-02, RES-03).

Deterministic, offline-first news/fundamentals researcher for scanner
candidates. Produces the PRD "Research Result" contract:

    {
        "symbol", "research_summary",
        "sentiment_score" (0..1), "confidence_score" (0..1),
        "red_flags": [], "citations": [{"date","headline","source","url"}],
    }

Design constraints:
  * Constructible and testable WITHOUT crewai / langchain / network.
  * Accepts an injectable :class:`~agents.llm.LLMClient`; defaults to
    :class:`~agents.llm.DeterministicLLM` so runs stay offline unless a real /
    fake client is supplied.
  * Accepts an injectable :class:`~tools.web_tools.WebResearchSource`; defaults
    to :class:`~tools.web_tools.StubWebSource` (offline canned data).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from agents.llm import DeterministicLLM, LLMClient
from tools.web_tools import StubWebSource, WebResearchSource

# ---------------------------------------------------------------------------
# Keyword heuristics (documented word lists).
#
# Sentiment is a transparent lexical score: positive keywords raise it,
# negative + red-flag keywords lower it. Matching is whole-word (token based),
# case-insensitive, over headline + snippet text.
# ---------------------------------------------------------------------------
POSITIVE_WORDS = frozenset(
    {
        "surge", "surges", "surged", "record", "profit", "profits",
        "upgrade", "upgrades", "upgraded", "beat", "beats", "growth",
        "gain", "gains", "gained", "rally", "rallies", "rallied",
        "expansion", "expands", "win", "wins", "won", "strong",
        "outperform", "bullish", "boost", "boosts", "jump", "jumps",
        "rise", "rises", "rose", "soar", "soars", "high", "highs",
        "robust", "upbeat", "approves", "approved",
    }
)

# Soft negatives — lower sentiment but are not, on their own, red flags.
NEGATIVE_WORDS = frozenset(
    {
        "loss", "losses", "decline", "declines", "fall", "falls", "fell",
        "weak", "miss", "misses", "missed", "cut", "cuts", "plunge",
        "plunges", "slump", "slumps", "concern", "concerns", "warning",
        "bearish", "drop", "drops", "slip", "slips", "lower", "sell",
        "underperform", "sluggish", "delay", "delays",
    }
)

# Hard red-flag keywords — recorded as explicit red flags AND lower sentiment.
RED_FLAG_WORDS = frozenset(
    {
        "fraud", "probe", "default", "defaults", "downgrade", "downgrades",
        "downgraded", "scam", "scandal", "investigation", "raid", "raids",
        "insolvency", "bankruptcy", "embezzlement", "lawsuit", "penalty",
        "fine", "resignation", "resigns", "misconduct", "manipulation",
    }
)

# Sentiment tuning.
_NEUTRAL = 0.5
_POS_STEP = 0.08
_NEG_STEP = 0.10


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z]+", str(text).lower())


@dataclass
class Citation:
    """A single news reference backing the research summary."""

    date: str = ""
    headline: str = ""
    source: str = ""
    url: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "date": self.date,
            "headline": self.headline,
            "source": self.source,
            "url": self.url,
        }


@dataclass
class ResearchResult:
    """PRD Research Result contract."""

    symbol: str
    research_summary: str = ""
    sentiment_score: float = _NEUTRAL
    confidence_score: float = 0.5
    red_flags: List[str] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "research_summary": self.research_summary,
            "sentiment_score": self.sentiment_score,
            "confidence_score": self.confidence_score,
            "red_flags": list(self.red_flags),
            "citations": [c.to_dict() for c in self.citations],
        }


class ResearchAgent:
    """Deterministic research agent with optional LLM enrichment."""

    SYSTEM_PROMPT = (
        "You are an equity research analyst. Given recent news headlines for a "
        "stock, write a concise, factual 2-3 sentence research summary. Do not "
        "invent facts beyond the headlines provided."
    )

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        web_source: Optional[WebResearchSource] = None,
        model: Optional[str] = None,
        staleness_days: int = 120,
    ):
        self.llm_client: LLMClient = llm_client or DeterministicLLM()
        self.web_source: WebResearchSource = web_source or StubWebSource()
        self.model = model
        self.staleness_days = int(staleness_days)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def research(self, symbol_or_candidate: Any, fundamentals: Any = None) -> ResearchResult:
        """Research a single symbol or scanner-candidate-like object."""

        symbol = self._extract_symbol(symbol_or_candidate)

        news = self._fetch_news(symbol)
        citations = [
            Citation(
                date=str(item.get("date", "")),
                headline=str(item.get("headline", "")),
                source=str(item.get("source", "")),
                url=str(item.get("url", "")),
            )
            for item in news
        ]

        sentiment = self._sentiment(news)
        red_flags = self._red_flags(news, citations, symbol, fundamentals)
        confidence = self._confidence(citations, red_flags)
        summary = self._summary(symbol, citations, sentiment, red_flags)

        return ResearchResult(
            symbol=symbol,
            research_summary=summary,
            sentiment_score=round(sentiment, 4),
            confidence_score=round(confidence, 4),
            red_flags=red_flags,
            citations=citations,
        )

    def research_batch(self, items: Sequence[Any]) -> List[ResearchResult]:
        return [self.research(item) for item in items]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_symbol(item: Any) -> str:
        if isinstance(item, str):
            return item.strip()
        symbol = getattr(item, "symbol", None)
        return str(symbol).strip() if symbol else ""

    def _fetch_news(self, symbol: str) -> List[Dict]:
        if not symbol:
            return []
        try:
            news = self.web_source.search_news(symbol)
        except Exception:
            return []
        return list(news or [])

    def _sentiment(self, news: List[Dict]) -> float:
        """Lexical sentiment in [0, 1]; defaults to neutral 0.5 with no news.

        Starts at 0.5, adds ``_POS_STEP`` per positive-keyword hit and subtracts
        ``_NEG_STEP`` per negative/red-flag-keyword hit, then clamps to [0, 1].
        """

        if not news:
            return _NEUTRAL

        negatives = NEGATIVE_WORDS | RED_FLAG_WORDS
        pos_hits = 0
        neg_hits = 0
        for item in news:
            text = f"{item.get('headline', '')} {item.get('snippet', '')}"
            for token in _tokens(text):
                if token in POSITIVE_WORDS:
                    pos_hits += 1
                elif token in negatives:
                    neg_hits += 1

        score = _NEUTRAL + _POS_STEP * pos_hits - _NEG_STEP * neg_hits
        return _clamp(score)

    def _red_flags(
        self,
        news: List[Dict],
        citations: List[Citation],
        symbol: str,
        fundamentals: Any,
    ) -> List[str]:
        flags: List[str] = []

        # RES-03: no citations => cannot corroborate.
        if not citations:
            flags.append("no_citations")

        # RES-03: adverse-keyword headlines.
        for item in news:
            headline = str(item.get("headline", ""))
            hits = sorted({t for t in _tokens(headline) if t in RED_FLAG_WORDS})
            for word in hits:
                flags.append(f"adverse_news[{word}]: {headline}")

        # RES-03: fundamentals staleness / missing date.
        flags.extend(self._fundamentals_flags(symbol, fundamentals))

        return flags

    def _fundamentals_flags(self, symbol: str, fundamentals: Any) -> List[str]:
        fund = fundamentals
        if fund is None:
            # Fall back to the injected web source snapshot (offline stub => {}).
            try:
                fund = self.web_source.get_fundamentals_snapshot(symbol)
            except Exception:
                fund = None

        if not fund:  # None, empty dict, or empty object => nothing to check.
            return []

        as_of = fund.get("as_of") if isinstance(fund, dict) else getattr(fund, "as_of", None)
        if not as_of:
            return ["missing_fundamentals_date"]

        try:
            as_of_date = date.fromisoformat(str(as_of)[:10])
        except ValueError:
            return ["missing_fundamentals_date"]

        age_days = (date.today() - as_of_date).days
        if age_days > self.staleness_days:
            return ["stale_fundamentals"]
        return []

    @staticmethod
    def _confidence(citations: List[Citation], red_flags: List[str]) -> float:
        """Confidence rises with corroborating citations, falls per red flag."""

        confidence = _NEUTRAL + 0.12 * min(len(citations), 3)
        confidence -= 0.15 * len(red_flags)
        return _clamp(confidence)

    def _summary(
        self,
        symbol: str,
        citations: List[Citation],
        sentiment: float,
        red_flags: List[str],
    ) -> str:
        """LLM summary when the client returns text; deterministic fallback otherwise."""

        llm_summary = self._llm_summary(symbol, citations, sentiment)
        if llm_summary and llm_summary.strip():
            return llm_summary.strip()
        return self._deterministic_summary(symbol, citations, sentiment, red_flags)

    def _llm_summary(
        self,
        symbol: str,
        citations: List[Citation],
        sentiment: float,
    ) -> str:
        headlines = "\n".join(
            f"- {c.headline} ({c.source}, {c.date})" for c in citations
        ) or "- (no recent news found)"
        prompt = (
            f"Symbol: {symbol}\n"
            f"Lexical sentiment score (0..1): {sentiment:.2f}\n"
            f"Recent headlines:\n{headlines}\n\n"
            "Write the research summary."
        )
        try:
            return self.llm_client.complete(prompt, system=self.SYSTEM_PROMPT) or ""
        except Exception:
            return ""

    @staticmethod
    def _deterministic_summary(
        symbol: str,
        citations: List[Citation],
        sentiment: float,
        red_flags: List[str],
    ) -> str:
        if sentiment >= 0.6:
            label = "positive"
        elif sentiment <= 0.4:
            label = "negative"
        else:
            label = "neutral"

        parts = [
            f"{symbol}: sentiment {label} ({sentiment:.2f}) based on "
            f"{len(citations)} recent news item(s)."
        ]
        if citations:
            top = citations[0]
            parts.append(f'Top headline: "{top.headline}" ({top.source}).')
        if red_flags:
            parts.append("Red flags: " + "; ".join(red_flags) + ".")
        else:
            parts.append("No red flags detected.")
        return " ".join(parts)
