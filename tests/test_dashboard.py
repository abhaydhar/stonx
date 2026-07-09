"""
Unit tests for the Streamlit dashboard (app.py) -- PRD Wave 5 (UI-01..UI-07).

These tests exercise ONLY the pure data functions in ``app``. They never launch
Streamlit; importing ``app`` here proves there are no import-time ``st.*`` calls.

Run with:
    pytest tests/test_dashboard.py -q
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402  (import-after-path-setup is intentional)
from modules.journal import TradeJournal  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_scan() -> dict:
    return {
        "timestamp": "2026-07-08T10:00:00",
        "market_regime": {"regime": "bull", "is_bull_market": True},
        "funnel_counts": {"universe_total": 3, "approved": 2, "rejected": 1},
        "candidates": [
            {
                "rank": 1,
                "symbol": "AAA.NS",
                "pattern": "consolidation_after_uptrend",
                "confidence": 0.8,
                "entry": 100.0,
                "stop": 90.0,
                "target": 140.0,
                "rr_ratio": 4.0,
                "position_shares": 100,
                "position_inr": 10000.0,
                "capital_at_risk_inr": 1000.0,
                "capital_at_risk_pct": 0.001,
                "sector": "Tech",
                "market_regime": "bull",
                "risk_status": "approved",
                "rationale": "ok",
            },
            {
                "rank": 2,
                "symbol": "BBB.NS",
                "pattern": "higher_lows",
                "confidence": 0.6,
                "entry": 200.0,
                "stop": 190.0,
                "target": 215.0,
                "rr_ratio": 1.5,
                "position_shares": 50,
                "position_inr": 10000.0,
                "capital_at_risk_inr": 500.0,
                "capital_at_risk_pct": 0.0005,
                "sector": "FMCG",
                "market_regime": "bull",
                "risk_status": "rejected",
                "rationale": "low rr",
            },
        ],
        "rejected": [],
        "data_quality": {},
    }


@pytest.fixture()
def journal() -> TradeJournal:
    """In-memory journal seeded with a candidate, decision, and trades."""
    j = TradeJournal(db_url="sqlite:///:memory:")
    j.record_agent_decision(
        run_id="run1",
        symbol="AAA.NS",
        agent_name="research",
        decision="approve",
        reasoning="strong fundamentals",
        confidence=0.75,
    )
    pos_id = j.open_position(
        symbol="AAA.NS",
        entry_price=100.0,
        stop_price=90.0,
        target_price=140.0,
        shares=100,
        sector="Tech",
    )
    other = j.open_position(
        symbol="BBB.NS",
        entry_price=200.0,
        stop_price=190.0,
        target_price=230.0,
        shares=50,
        sector="FMCG",
    )
    j.close_trade(other, exit_price=230.0, exit_reason="target hit")
    return j


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------


def test_app_imports_without_streamlit_side_effects():
    # If importing app triggered st.* calls, the import above would have failed
    # outside a Streamlit runtime. It is enough that the pure functions exist.
    for name in (
        "load_scan_output",
        "scan_candidates_df",
        "filter_candidates",
        "reasoning_df",
        "open_positions_df",
        "closed_trades_df",
        "journal_summary",
        "learning_view",
        "main",
    ):
        assert hasattr(app, name)


# ---------------------------------------------------------------------------
# load_scan_output
# ---------------------------------------------------------------------------


def test_load_scan_output_missing_path_returns_empty(tmp_path):
    assert app.load_scan_output(tmp_path / "does_not_exist.json") == {}
    assert app.load_scan_output(None) == {}


def test_load_scan_output_invalid_json_returns_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert app.load_scan_output(bad) == {}


def test_load_scan_output_reads_written_sample(tmp_path):
    path = tmp_path / "scan_results_20260708.json"
    sample = _sample_scan()
    path.write_text(json.dumps(sample), encoding="utf-8")

    loaded = app.load_scan_output(path)
    assert loaded["market_regime"]["regime"] == "bull"
    assert len(loaded["candidates"]) == 2


# ---------------------------------------------------------------------------
# scan_candidates_df
# ---------------------------------------------------------------------------


def test_scan_candidates_df_columns_and_rowcount():
    df = app.scan_candidates_df(_sample_scan())
    assert list(df.columns) == app.SCAN_DISPLAY_COLUMNS
    assert len(df) == 2
    assert set(df["symbol"]) == {"AAA.NS", "BBB.NS"}


def test_scan_candidates_df_empty_scan():
    df = app.scan_candidates_df({})
    assert list(df.columns) == app.SCAN_DISPLAY_COLUMNS
    assert df.empty


# ---------------------------------------------------------------------------
# filter_candidates
# ---------------------------------------------------------------------------


def test_filter_candidates_min_rr_drops_low_rr():
    df = app.scan_candidates_df(_sample_scan())
    out = app.filter_candidates(df, min_rr=3.0)
    assert list(out["symbol"]) == ["AAA.NS"]


def test_filter_candidates_sector():
    df = app.scan_candidates_df(_sample_scan())
    out = app.filter_candidates(df, sector="FMCG")
    assert list(out["symbol"]) == ["BBB.NS"]


def test_filter_candidates_pattern():
    df = app.scan_candidates_df(_sample_scan())
    out = app.filter_candidates(df, pattern="higher_lows")
    assert list(out["symbol"]) == ["BBB.NS"]


def test_filter_candidates_approved_only():
    df = app.scan_candidates_df(_sample_scan())
    out = app.filter_candidates(df, approved_only=True)
    assert list(out["symbol"]) == ["AAA.NS"]


def test_filter_candidates_empty_df_no_crash():
    empty = pd.DataFrame()
    out = app.filter_candidates(empty, min_rr=3.0, sector="Tech", approved_only=True)
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    # None also degrades safely
    assert app.filter_candidates(None).empty


def test_filter_candidates_volume_confirmed_when_column_present():
    df = pd.DataFrame(
        {"symbol": ["X", "Y"], "volume_confirmed": [True, False]}
    )
    out = app.filter_candidates(df, volume_confirmed=True)
    assert list(out["symbol"]) == ["X"]


# ---------------------------------------------------------------------------
# journal-backed functions
# ---------------------------------------------------------------------------


def test_reasoning_df(journal):
    df = app.reasoning_df(journal)
    assert list(df.columns) == app.REASONING_COLUMNS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["symbol"] == "AAA.NS"
    assert row["agent_name"] == "research"
    assert row["decision"] == "approve"

    # symbol filter for a symbol with no decisions -> empty, still typed
    empty = app.reasoning_df(journal, symbol="ZZZ.NS")
    assert list(empty.columns) == app.REASONING_COLUMNS
    assert empty.empty


def test_open_positions_df(journal):
    df = app.open_positions_df(journal)
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "AAA.NS"


def test_closed_trades_df(journal):
    df = app.closed_trades_df(journal)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["symbol"] == "BBB.NS"
    assert row["outcome"] == "win"
    assert row["pnl"] == pytest.approx((230.0 - 200.0) * 50)


def test_journal_summary(journal):
    summary = app.journal_summary(journal)
    assert summary["open_count"] == 1
    assert summary["total_closed"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# learning_view
# ---------------------------------------------------------------------------


def test_learning_view_returns_dict_with_status(journal):
    view = app.learning_view(journal)
    assert isinstance(view, dict)
    assert "status" in view
    assert "recommendations" in view
    assert isinstance(view["recommendations"], list)


def test_learning_view_survives_import_failure(monkeypatch, journal):
    # Force the lazy import inside learning_view to fail.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "agents.learning_agent" or name.startswith("agents.learning_agent"):
            raise ImportError("forced failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    view = app.learning_view(journal)
    assert view == {"status": "unavailable", "recommendations": []}
