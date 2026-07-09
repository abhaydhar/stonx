"""
Unit tests for agents/execution_agent.py (PRD EXEC-01..03).

Each test uses an isolated temp-file SQLite journal (via pytest ``tmp_path``)
so there is no shared state and the project DB is never touched. No network:
prices are supplied through DictPriceProvider / plain callables.
"""

import sys
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.execution_agent import (
    DictPriceProvider,
    ExecutionAgent,
    ExecutionEvent,
)
from modules.journal import TradeJournal
from tools.alert_tools import AlertSender


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(db_url=f"sqlite:///{tmp_path.as_posix()}/exec.db")


def _open_position(journal):
    """Standard test position: entry 100, stop 90, target 130, 100 shares."""
    return journal.open_position(
        symbol="AAA.NS",
        entry_price=100.0,
        stop_price=90.0,
        target_price=130.0,
        shares=100,
        sector="Tech",
    )


# ---------------------------------------------------------------------------
# EXEC-02: stop / target / open
# ---------------------------------------------------------------------------


def test_stop_breached_closes_position(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 85.0}))

    events = agent.check_positions()

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "STOP_BREACHED"
    assert ev.journal_updated is True
    assert ev.exit_price == 90.0  # effective stop
    assert ev.pnl is not None and ev.pnl < 0
    assert ev.pnl_percent < 0
    assert ev.alert_sent is False  # no sender wired

    assert journal.get_open_positions() == []
    closed = journal.get_closed_trades()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop"
    assert closed[0]["outcome"] == "loss"


def test_target_hit_closes_position(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 130.0}))

    events = agent.check_positions()

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "TARGET_HIT"
    assert ev.journal_updated is True
    assert ev.exit_price == 130.0
    assert ev.pnl > 0
    assert ev.pnl_percent > 0

    assert journal.get_open_positions() == []
    closed = journal.get_closed_trades()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "win"


def test_open_event_changes_nothing(journal):
    _open_position(journal)
    # Price 105: no breach, and < 1R (needs 110) so no trailing either.
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 105.0}))

    events = agent.check_positions()

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "OPEN"
    assert ev.journal_updated is False
    assert ev.exit_price is None

    open_positions = journal.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["current_stop"] == 90.0  # unchanged
    assert journal.get_closed_trades() == []


# ---------------------------------------------------------------------------
# EXEC-03: trailing stop to breakeven after +1R
# ---------------------------------------------------------------------------


def test_trailing_stop_moves_to_breakeven(journal):
    _open_position(journal)
    # Price 111 has advanced >= 1R (1R = 100 - 90 = 10 -> trigger at 110).
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 111.0}))

    events = agent.check_positions()

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "TRAILING_STOP_MOVED"
    assert ev.journal_updated is True
    assert ev.exit_price is None
    # Unrealized P&L is reported for the still-open position.
    assert ev.pnl == pytest.approx((111.0 - 100.0) * 100)

    open_positions = journal.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["current_stop"] == 100.0  # moved to breakeven
    assert journal.get_closed_trades() == []


def test_trailing_disabled_yields_open(journal):
    _open_position(journal)
    agent = ExecutionAgent(
        journal, DictPriceProvider({"AAA.NS": 111.0}), one_r_trail=False
    )

    events = agent.check_positions()

    assert events[0].event_type == "OPEN"
    assert journal.get_open_positions()[0]["current_stop"] == 90.0


def test_trailing_not_repeated_once_at_breakeven(journal):
    """After the stop is at breakeven, a further small advance is just OPEN."""
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 111.0}))
    agent.check_positions()  # first pass: trail to breakeven

    # Second pass at a higher price still above breakeven but effective_stop
    # (100) is no longer < entry (100), so no further move.
    agent2 = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 115.0}))
    events = agent2.check_positions()
    assert events[0].event_type == "OPEN"
    assert journal.get_open_positions()[0]["current_stop"] == 100.0


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_stop_breach_dispatches_alert_without_live_send(journal):
    _open_position(journal)
    sender = AlertSender(dry_run=True)  # dry-run: never a live send
    agent = ExecutionAgent(
        journal, DictPriceProvider({"AAA.NS": 85.0}), alert_sender=sender
    )

    events = agent.check_positions()

    ev = events[0]
    assert ev.event_type == "STOP_BREACHED"
    assert ev.alert_sent is True  # dispatched to sender


def test_open_event_does_not_alert(journal):
    _open_position(journal)
    sender = AlertSender(dry_run=True)
    agent = ExecutionAgent(
        journal, DictPriceProvider({"AAA.NS": 105.0}), alert_sender=sender
    )

    ev = agent.check_positions()[0]
    assert ev.event_type == "OPEN"
    assert ev.alert_sent is False


# ---------------------------------------------------------------------------
# Price-provider abstraction / edge cases
# ---------------------------------------------------------------------------


def test_callable_price_provider(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, lambda symbol: 130.0)
    events = agent.check_positions()
    assert events[0].event_type == "TARGET_HIT"


def test_missing_price_is_skipped(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({}))  # no price for symbol
    events = agent.check_positions()
    assert events == []
    assert len(journal.get_open_positions()) == 1


# ---------------------------------------------------------------------------
# Contract + convenience
# ---------------------------------------------------------------------------


def test_execution_event_to_dict_contract(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 85.0}))
    ev = agent.check_positions()[0]
    d = ev.to_dict()
    assert set(d.keys()) == {
        "symbol",
        "event_type",
        "entry_price",
        "exit_price",
        "pnl",
        "pnl_percent",
        "alert_sent",
        "journal_updated",
    }
    assert isinstance(ev, ExecutionEvent)


def test_run_once_returns_events_and_summary(journal):
    _open_position(journal)
    agent = ExecutionAgent(journal, DictPriceProvider({"AAA.NS": 85.0}))
    out = agent.run_once()
    assert set(out.keys()) == {"events", "summary"}
    assert len(out["events"]) == 1
    assert out["summary"]["total_closed"] == 1
    assert out["summary"]["open_count"] == 0
