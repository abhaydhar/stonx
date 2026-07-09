"""
Full-system integration tests (QA-02).

Everything runs offline with mocked externals (no network, no LLM key, no
crewai/langchain). Two flows are exercised:

1. scanner output -> research -> risk -> journal persistence -> execution
   monitoring -> dry-run alert (the multi-agent + persistence + execution spine).
2. the REAL deterministic scanner funnel driven by a fake OHLCV provider, proving
   ingestion -> fundamental -> pattern -> volume -> risk -> JSON/CSV integrate.
"""

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.execution_agent import DictPriceProvider, ExecutionAgent
from agents.research_agent import ResearchAgent
from agents.risk_agent import RiskAgent
from modules.ingest import DataIngestion
from modules.journal import TradeJournal
from modules.scanner import (
    DeterministicScanner,
    MarketRegime,
    ScannerCandidate,
    ScannerOutput,
    write_scan_outputs,
)
from orchestrator.pipeline import ScanResearchRiskPipeline
from tools.alert_tools import AlertSender
from tools.web_tools import StubWebSource


# ---------------------------------------------------------------------------
# Flow 1: pipeline -> journal -> execution monitor -> alert
# ---------------------------------------------------------------------------

def _candidate(symbol, sector, entry, stop, target, rr, shares):
    return ScannerCandidate(
        rank=0, symbol=symbol, pattern="consolidation_after_uptrend", confidence=0.8,
        entry=entry, stop=stop, target=target, rr_ratio=rr, position_shares=shares,
        position_inr=entry * shares, capital_at_risk_inr=(entry - stop) * shares,
        capital_at_risk_pct=0.01, sector=sector, market_regime="bull",
    )


class _FixedScanner:
    def __init__(self, candidates):
        self._candidates = candidates

    def run(self, **kwargs):
        return ScannerOutput(
            timestamp="2026-07-09T16:00:00",
            market_regime=MarketRegime("bull", True, 2.5),
            funnel_counts={"approved": len(self._candidates)},
            candidates=list(self._candidates),
            rejected=[],
            data_quality={},
        )


def test_pipeline_to_execution_monitor_end_to_end(tmp_path):
    journal = TradeJournal(db_url=f"sqlite:///{tmp_path}/e2e.db")
    web = StubWebSource(
        records={
            "GOODCO.NS": [{
                "date": "2026-07-05",
                "headline": "GOODCO reports record profit and upgrade",
                "source": "Test", "url": "http://x/1", "snippet": "beat, growth",
            }]
        }
    )
    pipeline = ScanResearchRiskPipeline(
        scanner=_FixedScanner([_candidate("GOODCO.NS", "IT", 100.0, 90.0, 140.0, 5.0, 100)]),
        research_agent=ResearchAgent(web_source=web),
        risk_agent=RiskAgent(),
        journal=journal,
    )

    result = pipeline.run(persist=True)

    # Candidate approved and reasoning persisted
    assert result.approved, "expected at least one approved setup"
    decision = result.approved[0]
    assert decision.symbol == "GOODCO.NS"
    assert len(journal.get_agent_decisions(run_id=result.run_id)) == 2

    # Open the approved position in the journal
    pos_id = journal.open_position(
        symbol=decision.symbol, sector=decision.sector,
        entry_price=decision.entry, stop_price=decision.stop,
        target_price=decision.target, shares=decision.adjusted_shares or decision.position_shares,
        run_id=result.run_id, pattern=decision.pattern,
    )
    assert pos_id
    assert len(journal.get_open_positions()) == 1

    # Execution monitor: price gaps below stop -> STOP_BREACHED, dry-run alert
    sender = AlertSender(dry_run=True)
    agent = ExecutionAgent(journal, DictPriceProvider({"GOODCO.NS": 85.0}), alert_sender=sender)
    status = agent.run_once()

    events = status["events"]
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "STOP_BREACHED"
    assert event.journal_updated is True
    assert event.alert_sent is True          # dispatched to sender...
    # ...but nothing left the machine (dry-run)
    formatted = sender.send("t", "b")
    assert formatted["dry_run"] is True and formatted["sent"] is False

    # Journal reflects the closed loss
    assert journal.get_open_positions() == []
    closed = journal.get_closed_trades()
    assert len(closed) == 1
    assert closed[0]["pnl"] < 0
    summary = journal.summary()
    assert summary["total_closed"] == 1 and summary["open_count"] == 0


# ---------------------------------------------------------------------------
# Flow 2: real deterministic scanner funnel with a fake OHLCV provider
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Returns the same crafted breakout frame for any symbol. No network."""

    source_name = "fixture"
    adjusted = True

    def __init__(self, df):
        self._df = df

    def fetch(self, symbol, start_date, end_date):
        return self._df.copy()


def _breakout_frame():
    rng = np.random.default_rng(3)
    n_up, n_consol, hold = 65, 20, 2
    n = n_up + n_consol + hold
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    up = 1000.0 + np.linspace(0, 320, n_up) + rng.normal(0, 3, n_up)
    center = up[-1]
    consol = center + rng.uniform(-center * 0.025, center * 0.025, n_consol)
    chigh = consol.max() + 5
    breakout = np.array([chigh + 15, chigh + 22])
    close = np.concatenate([up, consol, breakout])
    noise = rng.uniform(3, 10, n)
    vol = rng.integers(500_000, 2_000_000, n).astype(float)
    vol[-2:] = float(vol[:-2].mean() * 3.0)
    return pd.DataFrame(
        {"Open": close - 2, "High": close + noise, "Low": close - noise,
         "Close": close, "Volume": vol},
        index=dates,
    )


def test_real_scanner_funnel_with_fake_provider(tmp_path):
    # Fundamentals fixture that passes the filter
    fpath = tmp_path / "fundamentals.csv"
    fpath.write_text(
        "symbol,as_of,sector,market_cap_cr,revenue_growth_pct,debt_to_equity,promoter_holding_pct\n"
        "AAA.NS,2026-06-30,IT,5000,15,0.2,60\n"
        "BBB.NS,2026-06-30,Auto,4000,10,0.3,55\n",
        encoding="utf-8",
    )
    upath = tmp_path / "universe.csv"
    upath.write_text("symbol,name,sector\nAAA.NS,AAA,IT\nBBB.NS,BBB,Auto\n", encoding="utf-8")

    from modules.fundamental import FundamentalFilter

    ingestion = DataIngestion(
        cache_dir=str(tmp_path / "cache"),
        provider=_FakeProvider(_breakout_frame()),
        universe_path=str(upath),
        max_missing_pct=1.0,
    )
    scanner = DeterministicScanner(
        ingestion=ingestion,
        fundamentals=FundamentalFilter(csv_path=str(fpath), min_market_cap_cr=500),
    )

    output = scanner.run(market_regime="bull", use_cache=False)

    # The full funnel ran end to end and produced structured output
    assert output.funnel_counts["universe_total"] == 2
    assert output.funnel_counts["passed_fundamental"] == 2
    assert output.funnel_counts["data_loaded"] == 2
    assert output.funnel_counts["passed_pattern"] >= 1
    assert "approved" in output.funnel_counts

    # JSON + CSV artifacts are written
    paths = write_scan_outputs(output, tmp_path / "out", basename="scan")
    assert paths["json"].exists() and paths["csv"].exists()
    assert "funnel_counts" in paths["json"].read_text(encoding="utf-8")
