"""
Research Agent tests (RES-01..RES-03) — fully offline and deterministic.

Runs without crewai / langchain / network. Exercises:
  * positive news -> sentiment > 0.5 with populated citations
  * NullWebSource -> "no_citations" red flag + reduced confidence
  * stale fundamentals -> "stale_fundamentals" red flag
  * negative "SEBI probe" headline -> red flag recorded + sentiment lowered
  * FakeLLM summary used; DeterministicLLM deterministic fallback still valid
  * to_dict() contract keys and score bounds
"""

import sys
from pathlib import Path
from types import SimpleNamespace

# Ensure project root on path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.llm import DeterministicLLM, FakeLLM
from agents.research_agent import Citation, ResearchAgent, ResearchResult
from tools.web_tools import NullWebSource, StubWebSource


CONTRACT_KEYS = {
    "symbol",
    "research_summary",
    "sentiment_score",
    "confidence_score",
    "red_flags",
    "citations",
}


def _positive_source():
    return StubWebSource(
        records={
            "GOODCO.NS": [
                {
                    "date": "2026-07-05",
                    "headline": "GoodCo surges to record profit as brokerage upgrades stock",
                    "source": "MoneyControl",
                    "url": "https://example.com/goodco-1",
                    "snippet": "Strong growth and robust order book; analysts stay bullish.",
                },
                {
                    "date": "2026-07-03",
                    "headline": "GoodCo gains on strong sales rally and upbeat guidance",
                    "source": "Economic Times",
                    "url": "https://example.com/goodco-2",
                    "snippet": "Expansion boosts outlook.",
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# Import safety: module must import without heavy optional deps installed.
# ---------------------------------------------------------------------------
def test_imports_are_offline_safe():
    import agents.research_agent as ra
    import tools.web_tools as wt

    assert hasattr(ra, "ResearchAgent")
    assert hasattr(wt, "WebResearchSource")


# ---------------------------------------------------------------------------
# Positive news
# ---------------------------------------------------------------------------
def test_positive_news_sentiment_and_citations():
    agent = ResearchAgent(web_source=_positive_source())
    result = agent.research("GOODCO.NS")

    assert isinstance(result, ResearchResult)
    assert result.sentiment_score > 0.5
    assert len(result.citations) == 2
    assert all(isinstance(c, Citation) for c in result.citations)
    assert result.citations[0].url.startswith("https://")
    assert "no_citations" not in result.red_flags


# ---------------------------------------------------------------------------
# NullWebSource -> no citations red flag + reduced confidence
# ---------------------------------------------------------------------------
def test_null_web_source_flags_no_citations_and_lowers_confidence():
    positive = ResearchAgent(web_source=_positive_source()).research("GOODCO.NS")
    empty = ResearchAgent(web_source=NullWebSource()).research("ANYTHING.NS")

    assert empty.citations == []
    assert "no_citations" in empty.red_flags
    assert empty.confidence_score < positive.confidence_score


# ---------------------------------------------------------------------------
# Stale fundamentals
# ---------------------------------------------------------------------------
def test_stale_fundamentals_red_flag():
    agent = ResearchAgent(web_source=_positive_source(), staleness_days=120)
    result = agent.research("GOODCO.NS", fundamentals={"as_of": "2000-01-01"})

    assert "stale_fundamentals" in result.red_flags


def test_fresh_fundamentals_no_stale_flag():
    agent = ResearchAgent(web_source=_positive_source(), staleness_days=120)
    result = agent.research("GOODCO.NS", fundamentals={"as_of": "2026-07-01"})

    assert "stale_fundamentals" not in result.red_flags


def test_missing_fundamentals_date_flag():
    agent = ResearchAgent(web_source=_positive_source())
    result = agent.research("GOODCO.NS", fundamentals={"promoter_holding_pct": 0.55})

    assert "missing_fundamentals_date" in result.red_flags


# ---------------------------------------------------------------------------
# Negative headline
# ---------------------------------------------------------------------------
def test_negative_headline_records_red_flag_and_lowers_sentiment():
    source = StubWebSource(
        records={
            "BADCO.NS": [
                {
                    "date": "2026-07-06",
                    "headline": "SEBI probe into BADCO accounting sends shares lower",
                    "source": "Reuters",
                    "url": "https://example.com/badco",
                    "snippet": "Regulator opens investigation; stock falls sharply.",
                }
            ]
        }
    )
    result = ResearchAgent(web_source=source).research("BADCO.NS")

    assert result.sentiment_score < 0.5
    assert any("probe" in flag for flag in result.red_flags)
    # no_citations must NOT be present since a citation exists.
    assert "no_citations" not in result.red_flags


# ---------------------------------------------------------------------------
# LLM summary vs deterministic fallback
# ---------------------------------------------------------------------------
def test_fake_llm_summary_is_used():
    agent = ResearchAgent(
        llm_client=FakeLLM(response="LLM summary text"),
        web_source=_positive_source(),
    )
    result = agent.research("GOODCO.NS")

    assert result.research_summary == "LLM summary text"


def test_deterministic_llm_falls_back_to_rule_based_summary():
    agent = ResearchAgent(llm_client=DeterministicLLM(), web_source=_positive_source())
    result = agent.research("GOODCO.NS")

    # Deterministic (empty LLM) fallback: non-empty, mentions the symbol.
    assert result.research_summary
    assert "GOODCO.NS" in result.research_summary


def test_empty_llm_response_falls_back():
    # FakeLLM with empty response must NOT overwrite deterministic text.
    agent = ResearchAgent(llm_client=FakeLLM(response=""), web_source=_positive_source())
    result = agent.research("GOODCO.NS")

    assert result.research_summary
    assert "GOODCO.NS" in result.research_summary


# ---------------------------------------------------------------------------
# Contract / bounds
# ---------------------------------------------------------------------------
def test_to_dict_contract_and_bounds():
    agent = ResearchAgent(web_source=_positive_source())
    payload = agent.research("GOODCO.NS").to_dict()

    assert set(payload.keys()) == CONTRACT_KEYS
    assert 0.0 <= payload["sentiment_score"] <= 1.0
    assert 0.0 <= payload["confidence_score"] <= 1.0
    assert isinstance(payload["red_flags"], list)
    assert isinstance(payload["citations"], list)
    for citation in payload["citations"]:
        assert set(citation.keys()) == {"date", "headline", "source", "url"}


def test_default_sentiment_neutral_when_no_news():
    result = ResearchAgent(web_source=NullWebSource()).research("UNKNOWN.NS")
    assert result.sentiment_score == 0.5


# ---------------------------------------------------------------------------
# Candidate-like input + batch
# ---------------------------------------------------------------------------
def test_accepts_scanner_candidate_like_object():
    candidate = SimpleNamespace(symbol="GOODCO.NS", sector="Auto")
    result = ResearchAgent(web_source=_positive_source()).research(candidate)

    assert result.symbol == "GOODCO.NS"
    assert len(result.citations) == 2


def test_research_batch_returns_one_result_per_item():
    agent = ResearchAgent(web_source=_positive_source())
    results = agent.research_batch(["GOODCO.NS", "UNKNOWN.NS"])

    assert len(results) == 2
    assert results[0].symbol == "GOODCO.NS"
    assert "no_citations" in results[1].red_flags
