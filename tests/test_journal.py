"""
Unit tests for the trade journal / persistence layer (PRD DB-04).

Every test uses an isolated temp SQLite database (built from pytest's
``tmp_path``) so there is no shared state between tests and the project's real
``./data/stonx.db`` is never touched.

Run with:
    pytest tests/test_journal.py -q
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.journal import (
    AgentDecision,
    Base,
    Candidate,
    ClosedTrade,
    OpenPosition,
    TradeJournal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path):
    """A TradeJournal backed by an isolated temp file SQLite DB."""
    db_url = f"sqlite:///{tmp_path.as_posix()}/test.db"
    return TradeJournal(db_url=db_url)


def _fake_scanner_output():
    """Build a ScannerOutput-like object with SimpleNamespace (no real scanner)."""
    regime = SimpleNamespace(regime="bull", is_bull_market=True)
    candidates = [
        SimpleNamespace(
            rank=1,
            symbol="AAA.NS",
            pattern="consolidation_after_uptrend",
            confidence=0.82,
            entry=100.0,
            stop=90.0,
            target=130.0,
            rr_ratio=3.0,
            position_shares=100,
            position_inr=10000.0,
            capital_at_risk_inr=1000.0,
            capital_at_risk_pct=0.01,
            sector="Tech",
            market_regime="bull",
        ),
        SimpleNamespace(
            rank=2,
            symbol="BBB.NS",
            pattern="range_tightening",
            confidence=0.71,
            entry=200.0,
            stop=185.0,
            target=260.0,
            rr_ratio=4.0,
            position_shares=50,
            position_inr=10000.0,
            capital_at_risk_inr=750.0,
            capital_at_risk_pct=0.0075,
            sector="FMCG",
            market_regime="bull",
        ),
    ]
    rejected = [
        SimpleNamespace(
            symbol="CCC.NS",
            stage="fundamental",
            reason="promoter_holding below minimum",
            sector="Energy",
            pattern=None,
            rr_ratio=None,
        ),
        SimpleNamespace(
            symbol="DDD.NS",
            stage="risk",
            reason="R:R 2.10 below minimum 2.50",
            sector="Auto",
            pattern="higher_lows",
            rr_ratio=2.1,
        ),
    ]
    return SimpleNamespace(
        timestamp="2026-07-08T16:00:00",
        market_regime=regime,
        funnel_counts={"approved": 2, "rejected": 2},
        candidates=candidates,
        rejected=rejected,
    )


# ---------------------------------------------------------------------------
# Schema / empty state
# ---------------------------------------------------------------------------


class TestSchemaAndEmptyState:
    def test_tables_created(self, journal):
        expected = {
            "candidates",
            "agent_decisions",
            "open_positions",
            "closed_trades",
        }
        assert expected.issubset(set(Base.metadata.tables))
        from sqlalchemy import inspect

        actual = set(inspect(journal.engine).get_table_names())
        assert expected.issubset(actual)

    def test_empty_queries_return_empty_lists(self, journal):
        assert journal.get_candidates() == []
        assert journal.get_agent_decisions() == []
        assert journal.get_open_positions() == []
        assert journal.get_closed_trades() == []

    def test_summary_empty_guards_div_by_zero(self, journal):
        summary = journal.summary()
        assert summary["total_closed"] == 0
        assert summary["win_rate"] == 0.0
        assert summary["avg_pnl_percent"] == 0.0
        assert summary["total_pnl"] == 0
        assert summary["open_count"] == 0

    def test_in_memory_engine_supported(self):
        mem = TradeJournal(db_url="sqlite:///:memory:")
        assert mem.get_candidates() == []
        pid = mem.open_position("ZZZ.NS", entry_price=10, stop_price=9, target_price=15, shares=10)
        # Same engine/connection is reused, so the row persists across calls.
        assert len(mem.get_open_positions()) == 1
        assert pid > 0


# ---------------------------------------------------------------------------
# Candidate history (DB-03)
# ---------------------------------------------------------------------------


class TestCandidateHistory:
    def test_record_scan_persists_candidates_and_rejected(self, journal):
        run_id = journal.record_scan(_fake_scanner_output())
        assert isinstance(run_id, str) and run_id

        all_rows = journal.get_candidates(run_id=run_id)
        assert len(all_rows) == 4  # 2 candidates + 2 rejected

        candidates = journal.get_candidates(run_id=run_id, status="candidate")
        assert len(candidates) == 2
        assert {c["symbol"] for c in candidates} == {"AAA.NS", "BBB.NS"}
        aaa = next(c for c in candidates if c["symbol"] == "AAA.NS")
        assert aaa["pattern"] == "consolidation_after_uptrend"
        assert aaa["entry"] == 100.0
        assert aaa["rr_ratio"] == 3.0
        assert aaa["market_regime"] == "bull"
        assert aaa["rejection_stage"] is None

    def test_record_scan_rejected_have_stage_and_reason(self, journal):
        run_id = journal.record_scan(_fake_scanner_output())

        rejected = journal.get_candidates(run_id=run_id, status="rejected")
        assert len(rejected) == 2
        by_symbol = {r["symbol"]: r for r in rejected}
        assert by_symbol["CCC.NS"]["rejection_stage"] == "fundamental"
        assert "promoter_holding" in by_symbol["CCC.NS"]["rejection_reason"]
        assert by_symbol["DDD.NS"]["rejection_stage"] == "risk"
        assert by_symbol["DDD.NS"]["rr_ratio"] == 2.1

    def test_record_scan_generates_run_id_when_absent(self, journal):
        run_id = journal.record_scan(_fake_scanner_output())
        # explicit run_id round-trips
        explicit = journal.record_scan(_fake_scanner_output(), run_id="custom-run-1")
        assert explicit == "custom-run-1"
        assert run_id != "custom-run-1"

        assert len(journal.get_candidates(run_id="custom-run-1")) == 4
        # querying without run_id returns everything from both scans
        assert len(journal.get_candidates()) == 8

    def test_get_candidates_status_filter(self, journal):
        journal.record_scan(_fake_scanner_output())
        assert len(journal.get_candidates(status="candidate")) == 2
        assert len(journal.get_candidates(status="rejected")) == 2


# ---------------------------------------------------------------------------
# Agent reasoning
# ---------------------------------------------------------------------------


class TestAgentDecisions:
    def test_record_and_roundtrip_payload_json(self, journal):
        payload = {
            "symbol": "AAA.NS",
            "approval_status": "APPROVED",
            "concerns": ["thin liquidity"],
            "position_size_multiplier": 0.7,
            "confidence_after_challenge": 0.68,
        }
        decision_id = journal.record_agent_decision(
            run_id="run-1",
            symbol="AAA.NS",
            agent_name="risk",
            decision="APPROVED",
            reasoning="R:R acceptable after volatility check.",
            confidence=0.68,
            payload=payload,
        )
        assert isinstance(decision_id, int) and decision_id > 0

        rows = journal.get_agent_decisions(run_id="run-1", symbol="AAA.NS")
        assert len(rows) == 1
        row = rows[0]
        assert row["agent_name"] == "risk"
        assert row["decision"] == "APPROVED"
        assert row["confidence"] == 0.68
        assert row["reasoning"].startswith("R:R acceptable")
        # payload JSON round-trips to the original dict
        assert row["payload"] == payload

    def test_filter_by_symbol_and_run(self, journal):
        journal.record_agent_decision("run-1", "AAA.NS", "research", "POSITIVE", payload={"a": 1})
        journal.record_agent_decision("run-1", "BBB.NS", "risk", "REJECTED", payload=None)
        journal.record_agent_decision("run-2", "AAA.NS", "risk", "CONDITIONAL")

        assert len(journal.get_agent_decisions()) == 3
        assert len(journal.get_agent_decisions(run_id="run-1")) == 2
        assert len(journal.get_agent_decisions(symbol="AAA.NS")) == 2
        assert len(journal.get_agent_decisions(run_id="run-1", symbol="AAA.NS")) == 1
        # None payload decodes to None
        rej = journal.get_agent_decisions(symbol="BBB.NS")[0]
        assert rej["payload"] is None


# ---------------------------------------------------------------------------
# Trade lifecycle (DB-02)
# ---------------------------------------------------------------------------


class TestTradeLifecycle:
    def test_open_update_close_flow(self, journal):
        pid = journal.open_position(
            symbol="AAA.NS",
            entry_price=100.0,
            stop_price=90.0,
            target_price=130.0,
            shares=100,
            sector="Tech",
            pattern="consolidation_after_uptrend",
            run_id="run-1",
        )
        assert isinstance(pid, int) and pid > 0

        opens = journal.get_open_positions()
        assert len(opens) == 1
        assert opens[0]["current_stop"] == 90.0  # defaults to stop_price

        # Trail the stop to breakeven
        updated = journal.update_position(pid, current_stop=100.0, notes="moved to BE")
        assert updated["current_stop"] == 100.0
        assert updated["notes"] == "moved to BE"

        # Close at a profit: entry 100, exit 120, 100 shares -> pnl 2000, pnl% 20.0
        closed = journal.close_trade(
            pid, exit_price=120.0, exit_reason="target hit", outcome="target"
        )
        assert closed["pnl"] == pytest.approx(2000.0)
        assert closed["pnl_percent"] == pytest.approx(20.0)
        assert closed["outcome"] == "target"
        assert closed["exit_reason"] == "target hit"
        assert closed["symbol"] == "AAA.NS"
        assert closed["pattern"] == "consolidation_after_uptrend"

        # After close: open_positions empty, closed_trades has 1 row
        assert journal.get_open_positions() == []
        closed_rows = journal.get_closed_trades()
        assert len(closed_rows) == 1
        assert closed_rows[0]["pnl"] == pytest.approx(2000.0)

    def test_losing_trade_negative_pnl(self, journal):
        pid = journal.open_position(
            symbol="BBB.NS",
            entry_price=200.0,
            stop_price=185.0,
            target_price=260.0,
            shares=50,
        )
        closed = journal.close_trade(pid, exit_price=180.0, exit_reason="stopped out")
        # (180 - 200) * 50 = -1000
        assert closed["pnl"] == pytest.approx(-1000.0)
        assert closed["pnl_percent"] == pytest.approx(-10.0)
        assert closed["outcome"] == "loss"  # auto-derived from negative pnl
        assert journal.get_open_positions() == []
        assert len(journal.get_closed_trades()) == 1

    def test_default_capital_at_risk_computed(self, journal):
        pid = journal.open_position(
            symbol="CCC.NS",
            entry_price=100.0,
            stop_price=90.0,
            target_price=130.0,
            shares=100,
        )
        pos = journal.get_open_positions()[0]
        # |100 - 90| * 100 = 1000
        assert pos["capital_at_risk_inr"] == pytest.approx(1000.0)
        assert pid > 0

    def test_close_unknown_position_raises(self, journal):
        with pytest.raises(ValueError):
            journal.close_trade(999, exit_price=100.0)

    def test_update_rejects_unknown_field(self, journal):
        pid = journal.open_position("AAA.NS", 100.0, 90.0, 130.0, 100)
        with pytest.raises(ValueError):
            journal.update_position(pid, entry_price=999.0)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_win_rate_over_closed_trades(self, journal):
        # Two winners, one loser -> win_rate 2/3
        p1 = journal.open_position("AAA.NS", 100.0, 90.0, 130.0, 100)
        journal.close_trade(p1, exit_price=120.0)  # +2000
        p2 = journal.open_position("BBB.NS", 200.0, 185.0, 260.0, 50)
        journal.close_trade(p2, exit_price=220.0)  # +1000
        p3 = journal.open_position("CCC.NS", 50.0, 45.0, 70.0, 100)
        journal.close_trade(p3, exit_price=40.0)  # -1000

        # One position left open
        journal.open_position("DDD.NS", 300.0, 280.0, 360.0, 20)

        summary = journal.summary()
        assert summary["total_closed"] == 3
        assert summary["wins"] == 2
        assert summary["losses"] == 1
        assert summary["win_rate"] == pytest.approx(2 / 3)
        assert summary["total_pnl"] == pytest.approx(2000.0)
        assert summary["open_count"] == 1


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_separate_journals_do_not_share_state(self, tmp_path):
        j1 = TradeJournal(db_url=f"sqlite:///{tmp_path.as_posix()}/j1.db")
        j2 = TradeJournal(db_url=f"sqlite:///{tmp_path.as_posix()}/j2.db")
        j1.open_position("AAA.NS", 100.0, 90.0, 130.0, 100)
        assert len(j1.get_open_positions()) == 1
        assert j2.get_open_positions() == []
