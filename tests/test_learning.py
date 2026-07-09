"""
Unit tests for the Learning Agent (PRD Wave 7, LEARN-01..LEARN-04).

Everything is offline and deterministic: trades are plain hand-built dicts (or a
temp-file SQLite journal), and the one backtest-validation test uses a small
synthetic, seeded OHLCV fixture engineered so the real scanner detectors fire.

Run:
  pytest tests/test_learning.py -q
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.learning_agent import (
    INCREASE_RR_THRESHOLD,
    LearningAgent,
    LearningRecommendation,
    LearningReport,
)
from agents.llm import DeterministicLLM, FakeLLM
from modules.backtest import BacktestConfig, Backtester
from modules.journal import TradeJournal
from modules.learning import LearningStats, analyze_journal, analyze_trades

RECOMMENDATION_KEYS = {
    "finding",
    "action",
    "config_change",
    "backtest_validation",
    "auto_apply",
}


# ---------------------------------------------------------------------------
# Trade dict helpers
# ---------------------------------------------------------------------------

def _trade(pnl_percent, pattern="range_tightening", sector="Tech", outcome=None,
           entry=100.0, shares=10):
    """Build a journal-shaped closed-trade dict from a percent return."""
    if outcome is None:
        outcome = "win" if pnl_percent > 0 else ("loss" if pnl_percent < 0 else "breakeven")
    exit_price = entry * (1 + pnl_percent / 100.0)
    return {
        "symbol": "AAA.NS",
        "sector": sector,
        "entry_price": entry,
        "exit_price": exit_price,
        "shares": shares,
        "pnl": (exit_price - entry) * shares,
        "pnl_percent": pnl_percent,
        "outcome": outcome,
        "pattern": pattern,
    }


def _breakout_series(seed=0, outcome="win"):
    """Seeded OHLCV series engineered so the real detectors produce a trade.

    Mirrors the fixture shape used in tests/test_backtest.py.
    """
    rng = np.random.default_rng(seed)

    nA = 34
    a = rng.uniform(93, 97, nA)
    vol_a = rng.uniform(3.0, 4.0, nA)
    hl_a = rng.uniform(1.5, 2.2, nA)

    nB = 6
    b = np.array([101, 104, 108, 106, 102, 100.0]) + rng.normal(0, 0.2, nB)
    vol_b = rng.uniform(0.15, 0.30, nB)
    hl_b = rng.uniform(1.0, 1.6, nB)

    nC = 10
    c = 100.0 + rng.normal(0, 0.15, nC)
    vol_c = rng.uniform(0.4, 0.6, nC)
    hl_c = rng.uniform(0.15, 0.35, nC)

    close = np.concatenate([a, b, c])
    vol = np.concatenate([vol_a, vol_b, vol_c]) * 1e6
    hl = np.concatenate([hl_a, hl_b, hl_c])
    high = close + hl
    low = close - hl

    if outcome == "win":
        cont = np.linspace(101, 108, 8)
    else:
        cont = np.array([98.5, 96.0])
    chl = 0.8
    high = np.concatenate([high, cont + chl])
    low = np.concatenate([low, cont - chl])
    close = np.concatenate([close, cont])
    vol = np.concatenate([vol, rng.uniform(1.0, 1.5, len(cont)) * 1e6])

    n = len(close)
    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close - 0.1, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


def _bt_config():
    return BacktestConfig(min_rr=1.5, warmup_bars=49, max_holding_days=15,
                          volume_profile_bins=20)


def _sixty_trades():
    """60 closed trades with one clearly-underperforming pattern.

    * range_tightening (15): 5 wins @ +8%, 10 losses @ -6% -> expectancy < 0.
    * breakout (45): 30 wins @ +12%, 15 losses @ -5% -> healthy.
    Overall win rate = 35/60 = 0.583 (above the low-win-rate threshold).
    """
    trades = []
    trades += [_trade(8.0, pattern="range_tightening") for _ in range(5)]
    trades += [_trade(-6.0, pattern="range_tightening") for _ in range(10)]
    trades += [_trade(12.0, pattern="breakout") for _ in range(30)]
    trades += [_trade(-5.0, pattern="breakout") for _ in range(15)]
    return trades


# ---------------------------------------------------------------------------
# LEARN-01: analytics math (exact)
# ---------------------------------------------------------------------------

class TestAnalyzeTrades:
    def test_exact_metrics_with_losing_streak(self):
        # returns: +20, -10, -10 (streak), +10  (percent)
        trades = [
            _trade(20.0),
            _trade(-10.0),
            _trade(-10.0),
            _trade(10.0),
        ]
        s = analyze_trades(trades)

        assert s.total_trades == 4
        assert s.wins == 2
        assert s.losses == 2
        assert s.win_rate == pytest.approx(0.5)
        assert s.avg_win_pct == pytest.approx(15.0)          # (20 + 10) / 2
        assert s.avg_loss_pct == pytest.approx(-10.0)         # (-10 - 10) / 2
        assert s.expectancy == pytest.approx(2.5)             # 10 / 4
        assert s.profit_factor == pytest.approx(1.5)          # 30 / 20
        # equity: 1.2 (peak) -> 1.08 -> 0.972 -> 1.0692 ; dd = 0.228/1.2 = 0.19
        assert s.max_drawdown_pct == pytest.approx(0.19, abs=1e-6)

    def test_empty_is_safe_zeros(self):
        s = analyze_trades([])
        assert s.total_trades == 0
        assert s.win_rate == 0.0
        assert s.expectancy == 0.0
        assert s.profit_factor == 0.0
        assert s.max_drawdown_pct == 0.0
        assert s.by_pattern == {}
        assert s.to_dict()["total_trades"] == 0

    def test_profit_factor_infinite_when_no_losses(self):
        s = analyze_trades([_trade(5.0), _trade(10.0)])
        assert math.isinf(s.profit_factor)

    def test_by_pattern_breakdown_two_patterns(self):
        trades = [
            _trade(10.0, pattern="A"),
            _trade(-4.0, pattern="A"),
            _trade(6.0, pattern="B"),
            _trade(6.0, pattern="B"),
        ]
        s = analyze_trades(trades)
        assert set(s.by_pattern) == {"A", "B"}

        a = s.by_pattern["A"]
        assert a["trades"] == 2
        assert a["win_rate"] == pytest.approx(0.5)
        assert a["expectancy"] == pytest.approx(3.0)          # (10 - 4) / 2

        b = s.by_pattern["B"]
        assert b["trades"] == 2
        assert b["win_rate"] == pytest.approx(1.0)
        assert b["expectancy"] == pytest.approx(6.0)

    def test_by_sector_breakdown(self):
        trades = [
            _trade(10.0, sector="Tech"),
            _trade(-5.0, sector="Bank"),
        ]
        s = analyze_trades(trades)
        assert set(s.by_sector) == {"Tech", "Bank"}
        assert s.by_sector["Tech"]["expectancy"] == pytest.approx(10.0)
        assert s.by_sector["Bank"]["expectancy"] == pytest.approx(-5.0)

    def test_missing_fields_tolerated(self):
        # No pattern/sector/outcome -> defaults + pnl-sign classification.
        s = analyze_trades([{"pnl_percent": 5.0, "pnl": 50.0}])
        assert s.total_trades == 1
        assert s.wins == 1
        assert "unknown" in s.by_pattern
        assert "Unknown" in s.by_sector


# ---------------------------------------------------------------------------
# LEARN-03: minimum sample size gate
# ---------------------------------------------------------------------------

class TestMinimumSample:
    def test_insufficient_data_below_min_trades(self):
        trades = [_trade(5.0) for _ in range(10)]
        report = LearningAgent(min_trades=50).analyze(trades)
        assert isinstance(report, LearningReport)
        assert report.status == "insufficient_data"
        assert report.trades_analyzed == 10
        assert report.min_trades == 50
        assert report.recommendations == []
        assert report.stats is None
        assert "50" in report.notes

    def test_custom_min_trades_threshold(self):
        report = LearningAgent(min_trades=5).analyze([_trade(5.0) for _ in range(4)])
        assert report.status == "insufficient_data"


# ---------------------------------------------------------------------------
# LEARN-02: recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_sixty_trades_produce_recommendations(self):
        report = LearningAgent(min_trades=50).analyze(_sixty_trades())

        assert report.status == "ok"
        assert report.trades_analyzed == 60
        assert report.stats is not None
        assert len(report.recommendations) >= 1

        # Contract shape + never auto-apply.
        for rec in report.recommendations:
            assert isinstance(rec, LearningRecommendation)
            assert rec.auto_apply is False
            assert set(rec.to_dict().keys()) == RECOMMENDATION_KEYS

        # The under-performing range_tightening pattern must be flagged.
        rr_recs = [r for r in report.recommendations
                   if r.action == INCREASE_RR_THRESHOLD]
        assert rr_recs, "expected an INCREASE_RR_THRESHOLD recommendation"
        assert "range_tightening" in rr_recs[0].config_change["MIN_RR"]
        # base MIN_RR (2.5) bumped by rr_bump (1.0).
        assert rr_recs[0].config_change["MIN_RR"]["range_tightening"] == pytest.approx(3.5)

        # No backtester supplied -> validation empty + pending note.
        assert rr_recs[0].backtest_validation == {}
        assert "pending" in report.notes

    def test_low_win_rate_triggers_volume_recommendation(self):
        # 60 trades, mostly losers -> win rate well below 0.40.
        trades = [_trade(5.0, pattern="breakout") for _ in range(12)]
        trades += [_trade(-4.0, pattern="breakout") for _ in range(48)]
        report = LearningAgent(min_trades=50).analyze(trades)

        actions = {r.action for r in report.recommendations}
        assert "RAISE_VOLUME_CONFIRMATION" in actions
        vol_rec = next(r for r in report.recommendations
                       if r.action == "RAISE_VOLUME_CONFIRMATION")
        # base VSM (1.5) bumped by vsm_bump (0.5).
        assert vol_rec.config_change["VOLUME_SPIKE_MULTIPLIER"] == pytest.approx(2.0)

    def test_base_config_drives_bumped_value(self):
        report = LearningAgent(min_trades=50).analyze(
            _sixty_trades(), base_config=_bt_config()  # min_rr = 1.5
        )
        rr_rec = next(r for r in report.recommendations
                      if r.action == INCREASE_RR_THRESHOLD)
        # base min_rr 1.5 + 1.0 bump = 2.5
        assert rr_rec.config_change["MIN_RR"]["range_tightening"] == pytest.approx(2.5)

    def test_propose_config_changes_merges(self):
        agent = LearningAgent(min_trades=50)
        report = agent.analyze(_sixty_trades())
        merged = agent.propose_config_changes(report)
        assert "MIN_RR" in merged
        assert isinstance(merged["MIN_RR"], dict)
        assert "range_tightening" in merged["MIN_RR"]

    def test_fake_llm_phrases_finding(self):
        agent = LearningAgent(llm_client=FakeLLM(response="LLM phrased finding."),
                              min_trades=50)
        report = agent.analyze(_sixty_trades())
        rr_rec = next(r for r in report.recommendations
                      if r.action == INCREASE_RR_THRESHOLD)
        assert rr_rec.finding == "LLM phrased finding."

    def test_deterministic_llm_uses_default_finding(self):
        agent = LearningAgent(llm_client=DeterministicLLM(), min_trades=50)
        report = agent.analyze(_sixty_trades())
        rr_rec = next(r for r in report.recommendations
                      if r.action == INCREASE_RR_THRESHOLD)
        assert "range_tightening" in rr_rec.finding
        assert rr_rec.finding  # non-empty deterministic text


# ---------------------------------------------------------------------------
# LEARN-04: backtest validation
# ---------------------------------------------------------------------------

class TestBacktestValidation:
    def test_recommendation_has_before_after_metrics(self):
        price_data = {
            "W": _breakout_series(seed=3, outcome="win"),
            "L": _breakout_series(seed=101, outcome="loss"),
        }
        agent = LearningAgent(min_trades=50)
        report = agent.analyze(
            _sixty_trades(),
            backtester=Backtester(),
            price_data=price_data,
            base_config=_bt_config(),
        )

        assert report.status == "ok"
        assert report.recommendations
        for rec in report.recommendations:
            bv = rec.backtest_validation
            assert set(bv.keys()) == {"before", "after"}
            for side in ("before", "after"):
                assert "sharpe" in bv[side]
                assert "expectancy" in bv[side]
                assert isinstance(bv[side]["sharpe"], float)
                assert isinstance(bv[side]["expectancy"], float)


# ---------------------------------------------------------------------------
# analyze_journal path
# ---------------------------------------------------------------------------

class TestAnalyzeJournal:
    def test_journal_totals_match(self, tmp_path):
        journal = TradeJournal(db_url=f"sqlite:///{tmp_path.as_posix()}/l.db")

        # A winner: entry 100 -> exit 110 (+10%).
        pid1 = journal.open_position(
            symbol="AAA.NS", entry_price=100.0, stop_price=90.0,
            target_price=130.0, shares=10, sector="Tech", pattern="breakout",
        )
        journal.close_trade(pid1, exit_price=110.0, outcome="win")

        # A loser: entry 100 -> exit 95 (-5%).
        pid2 = journal.open_position(
            symbol="BBB.NS", entry_price=100.0, stop_price=90.0,
            target_price=130.0, shares=10, sector="Bank", pattern="range_tightening",
        )
        journal.close_trade(pid2, exit_price=95.0, outcome="loss")

        stats = analyze_journal(journal)
        assert isinstance(stats, LearningStats)
        assert stats.total_trades == 2
        assert stats.wins == 1
        assert stats.losses == 1
        assert stats.win_rate == pytest.approx(0.5)
        # expectancy = mean(+10, -5) = 2.5 percent
        assert stats.expectancy == pytest.approx(2.5)
        assert set(stats.by_pattern) == {"breakout", "range_tightening"}

    def test_agent_accepts_journal_directly(self, tmp_path):
        journal = TradeJournal(db_url=f"sqlite:///{tmp_path.as_posix()}/l2.db")
        pid = journal.open_position(
            symbol="AAA.NS", entry_price=100.0, stop_price=90.0,
            target_price=130.0, shares=10, sector="Tech",
        )
        journal.close_trade(pid, exit_price=110.0, outcome="win")

        report = LearningAgent(min_trades=50).analyze(journal)
        # Only 1 closed trade -> insufficient data.
        assert report.status == "insufficient_data"
        assert report.trades_analyzed == 1
