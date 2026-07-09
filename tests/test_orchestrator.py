"""
Tests for the deterministic scanner -> research -> risk pipeline (ORCH-01/02).

Fully offline: a fake scanner supplies candidates, real Research/Risk agents run
with DeterministicLLM + a stub web source, and reasoning is persisted to a temp
SQLite journal.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.research_agent import ResearchAgent
from agents.risk_agent import RiskAgent
from modules.journal import TradeJournal
from modules.scanner import MarketRegime, ScannerCandidate, ScannerOutput
from orchestrator.pipeline import ScanResearchRiskPipeline
from tools.web_tools import StubWebSource


def _candidate(symbol, sector, entry, stop, target, rr, shares=100, conf=0.8):
    return ScannerCandidate(
        rank=0,
        symbol=symbol,
        pattern="consolidation_after_uptrend",
        confidence=conf,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=rr,
        position_shares=shares,
        position_inr=entry * shares,
        capital_at_risk_inr=(entry - stop) * shares,
        capital_at_risk_pct=0.01,
        sector=sector,
        market_regime="bull",
    )


class FakeScanner:
    def __init__(self, candidates):
        self._candidates = candidates

    def run(self, **kwargs):
        return ScannerOutput(
            timestamp="2026-07-09T16:00:00",
            market_regime=MarketRegime(
                regime="bull", is_bull_market=True, min_rr_required=2.5
            ),
            funnel_counts={"universe_total": 2, "approved": len(self._candidates)},
            candidates=list(self._candidates),
            rejected=[],
            data_quality={},
        )


def _pipeline(tmp_path, candidates):
    web = StubWebSource(
        records={
            "CLEAN.NS": [
                {
                    "date": "2026-07-05",
                    "headline": "CLEAN posts record profit growth and strong orders",
                    "source": "Test",
                    "url": "http://example.com/clean",
                    "snippet": "beat estimates, upgrade",
                }
            ],
            "BADCO.NS": [
                {
                    "date": "2026-07-06",
                    "headline": "SEBI opens probe into BADCO accounting fraud",
                    "source": "Test",
                    "url": "http://example.com/badco",
                    "snippet": "investigation, default risk",
                }
            ],
        }
    )
    journal = TradeJournal(db_url=f"sqlite:///{tmp_path}/pipe.db")
    pipe = ScanResearchRiskPipeline(
        scanner=FakeScanner(candidates),
        research_agent=ResearchAgent(web_source=web),
        risk_agent=RiskAgent(),
        journal=journal,
    )
    return pipe, journal


def test_pipeline_runs_scanner_research_risk_and_persists(tmp_path):
    candidates = [
        _candidate("CLEAN.NS", "IT", 100.0, 90.0, 140.0, 5.0),
        _candidate("BADCO.NS", "Metals", 200.0, 190.0, 260.0, 6.0),
    ]
    pipe, journal = _pipeline(tmp_path, candidates)

    result = pipe.run(persist=True)

    # Flow produced one decision per candidate
    assert len(result.decisions) == 2
    by_symbol = {d.symbol: d for d in result.decisions}

    # Clean, positively-covered name is approved
    assert by_symbol["CLEAN.NS"].approval_status in {"APPROVED", "CONDITIONAL"}
    assert "CLEAN.NS" in {d.symbol for d in result.approved}

    # Adverse-news name is flagged and not cleanly approved
    bad = by_symbol["BADCO.NS"]
    assert bad.red_flags, "expected research red flags for adverse news"
    assert bad.approval_status in {"REJECTED", "CONDITIONAL"}
    assert bad.adjusted_shares <= bad.position_shares

    # ORCH-02: candidates + agent reasoning persisted and queryable by run
    stored_candidates = journal.get_candidates(run_id=result.run_id)
    assert len(stored_candidates) == 2

    decisions = journal.get_agent_decisions(run_id=result.run_id)
    agents = sorted({d["agent_name"] for d in decisions})
    assert agents == ["research", "risk"]
    assert len(decisions) == 4  # research + risk per symbol
    # payload round-trips as decoded JSON
    risk_rows = [d for d in decisions if d["agent_name"] == "risk"]
    assert all(isinstance(r["payload"], dict) for r in risk_rows)
    assert all("approval_status" in r["payload"] for r in risk_rows)


def test_pipeline_without_persistence_still_returns_run(tmp_path):
    candidates = [_candidate("CLEAN.NS", "IT", 100.0, 90.0, 140.0, 5.0)]
    pipe, journal = _pipeline(tmp_path, candidates)

    result = pipe.run(persist=False)

    assert result.run_id
    assert len(result.decisions) == 1
    # nothing written when persistence is off
    assert journal.get_candidates() == []
    assert journal.get_agent_decisions() == []


def test_pipeline_counts_summarize_decisions(tmp_path):
    candidates = [
        _candidate("CLEAN.NS", "IT", 100.0, 90.0, 140.0, 5.0),
        _candidate("BADCO.NS", "Metals", 200.0, 190.0, 260.0, 6.0),
    ]
    pipe, _ = _pipeline(tmp_path, candidates)

    result = pipe.run(persist=True)

    assert result.counts["scanned_candidates"] == 2
    assert result.counts["approved"] + result.counts["rejected_by_risk"] == 2
    assert result.to_dict()["run_id"] == result.run_id
