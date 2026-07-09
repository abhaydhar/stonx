"""
Unit tests for the historical backtesting framework (modules/backtest.py).

All fixtures are synthetic, deterministic (seeded numpy) OHLCV series -- no
network or file access.  Series are engineered so the *real* scanner detectors
(range_tightening pattern + volume-profile HVN/LVN) fire and produce trades
with known win / loss outcomes.

Run:
  pytest tests/test_backtest.py -q
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.backtest import (
    Backtester,
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    WalkForwardSplit,
    compute_equity_curve,
    evaluate_thresholds,
    generate_report,
    max_drawdown_from_equity,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------
#
# Structure of a "breakout" series (50 bars) engineered for the detectors:
#   * 34 volatile, HIGH-volume bars in the 93-97 band  -> HVN support ~97
#   * 6 low-volume bars wicking up to ~108 then back    -> thin LVN band above
#   * 10 tight bars around 100                          -> range_tightening fires
# Then continuation bars either rise (win) or fall to the stop (loss).

def _breakout_series(seed: int = 0, outcome: str = "win") -> pd.DataFrame:
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
        cont = np.linspace(101, 108, 8)          # rallies up to hit target ~105
    else:
        cont = np.array([98.5, 96.0])            # drops to hit stop ~97, ends there
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


def _long_series(seed: int = 7, n: int = 500) -> pd.DataFrame:
    """A long random-walk series for walk-forward splitting."""
    rng = np.random.default_rng(seed)
    close = 120.0 + np.abs(np.cumsum(rng.normal(0.05, 1.2, n)))
    hl = rng.uniform(0.5, 2.0, n)
    vol = rng.uniform(0.5, 4.0, n) * 1e6
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close + hl, "Low": close - hl, "Close": close, "Volume": vol},
        index=dates,
    )


def _run_config(**overrides) -> BacktestConfig:
    base = dict(min_rr=1.5, warmup_bars=49, max_holding_days=15, volume_profile_bins=20)
    base.update(overrides)
    return BacktestConfig(**base)


def _trade(pnl_pct: float, outcome: str = None, bars: int = 5, symbol: str = "T",
           exit_day: int = 1) -> BacktestTrade:
    """Construct a BacktestTrade with a chosen pnl_pct for metrics tests."""
    if outcome is None:
        outcome = "win" if pnl_pct > 0 else "loss"
    entry = 100.0
    exit_price = entry * (1 + pnl_pct)
    return BacktestTrade(
        symbol=symbol,
        pattern="range_tightening",
        entry_date=pd.Timestamp("2022-01-03"),
        entry=entry,
        stop=95.0,
        target=110.0,
        exit_date=pd.Timestamp("2022-01-03") + pd.Timedelta(days=exit_day),
        exit=exit_price,
        pnl=exit_price - entry,
        pnl_pct=pnl_pct,
        rr_planned=2.0,
        bars_held=bars,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_from_scanner_config_defaults(self):
        cfg = BacktestConfig.from_scanner_config()
        assert cfg.min_rr == pytest.approx(2.5)
        assert cfg.consolidation_range_pct == pytest.approx(0.08)
        assert cfg.volume_spike_multiplier == pytest.approx(1.5)
        assert cfg.warmup_bars == 120
        assert cfg.max_holding_days == 20

    def test_scanner_namespace_maps_attribute_names(self):
        cfg = BacktestConfig(consolidation_range_pct=0.05, volume_profile_bins=11)
        ns = cfg.to_scanner_namespace()
        assert ns.CONSOLIDATION_RANGE_PCT == pytest.approx(0.05)
        assert ns.VOLUME_PROFILE_BINS == 11


# ---------------------------------------------------------------------------
# Engine: run
# ---------------------------------------------------------------------------

class TestRun:
    def test_run_produces_trades_and_metrics(self):
        price_data = {f"W{i}": _breakout_series(seed=i, outcome="win") for i in range(3)}
        price_data.update({f"L{i}": _breakout_series(seed=100 + i, outcome="loss") for i in range(2)})

        result = Backtester().run(price_data, _run_config())

        assert isinstance(result, BacktestResult)
        assert len(result.trades) >= 1
        m = result.metrics
        assert m.total_trades >= 1
        assert m.wins >= 1                 # winning fixtures produced wins
        assert m.losses >= 1               # losing fixtures produced losses
        assert 0.0 <= m.win_rate <= 1.0
        assert isinstance(m.expectancy, float)
        # equity curve starts at 1.0 and has one point per closed trade
        assert result.equity_curve[0] == pytest.approx(1.0)
        assert len(result.equity_curve) == m.total_trades + 1
        assert result.period_start is not None and result.period_end is not None

    def test_single_win_fixture_produces_one_winning_trade(self):
        result = Backtester().run({"W": _breakout_series(seed=3, outcome="win")}, _run_config())
        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.outcome == "win"
        assert t.pnl_pct > 0
        assert t.exit == pytest.approx(t.target)

    def test_single_loss_fixture_produces_losing_trade(self):
        result = Backtester().run({"L": _breakout_series(seed=3, outcome="loss")}, _run_config())
        closed = [t for t in result.trades if t.outcome in ("win", "loss", "timeout")]
        assert len(closed) == 1
        assert closed[0].outcome == "loss"
        assert closed[0].pnl_pct < 0
        assert closed[0].exit == pytest.approx(closed[0].stop)

    def test_to_dict_is_json_serialisable(self):
        result = Backtester().run({"W": _breakout_series(seed=3, outcome="win")}, _run_config())
        d = result.to_dict()
        assert set(d.keys()) == {"config", "period", "trades", "metrics", "equity_curve"}
        # dates serialised to iso strings
        assert isinstance(d["trades"][0]["entry_date"], str)
        json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# Metrics math (hand-crafted, exact)
# ---------------------------------------------------------------------------

class TestMetricsMath:
    def test_exact_metrics_on_handcrafted_trades(self):
        # returns: +10%, -5%, +20%, -10%, +5%
        trades = [
            _trade(0.10, exit_day=1),
            _trade(-0.05, exit_day=2),
            _trade(0.20, exit_day=3),
            _trade(-0.10, exit_day=4),
            _trade(0.05, exit_day=5),
        ]
        m = BacktestMetrics.from_trades(trades)

        assert m.total_trades == 5
        assert m.wins == 3
        assert m.losses == 2
        assert m.win_rate == pytest.approx(0.6)
        # metrics are rounded to 6dp, so allow a small absolute tolerance
        assert m.avg_win_pct == pytest.approx((0.10 + 0.20 + 0.05) / 3, abs=1e-5)
        assert m.avg_loss_pct == pytest.approx((-0.05 - 0.10) / 2, abs=1e-5)
        # expectancy = mean of all returns = 0.20 / 5
        assert m.expectancy == pytest.approx(0.04, abs=1e-5)
        # profit_factor = 0.35 / 0.15
        assert m.profit_factor == pytest.approx(0.35 / 0.15, abs=1e-5)
        # equity peak 1.254 -> trough 1.1286 => 10% drawdown exactly
        assert m.max_drawdown_pct == pytest.approx(0.10, abs=1e-5)
        assert m.avg_bars_held == pytest.approx(5.0)

    def test_profit_factor_infinite_when_no_losses(self):
        m = BacktestMetrics.from_trades([_trade(0.05), _trade(0.10)])
        assert math.isinf(m.profit_factor)

    def test_timeout_with_negative_pnl_counts_as_loss(self):
        trades = [_trade(0.10, outcome="win"), _trade(-0.03, outcome="timeout")]
        m = BacktestMetrics.from_trades(trades)
        assert m.total_trades == 2
        assert m.wins == 1
        assert m.losses == 1

    def test_open_trades_excluded_from_metrics(self):
        trades = [_trade(0.10, outcome="win"), _trade(0.0, outcome="open")]
        m = BacktestMetrics.from_trades(trades)
        assert m.total_trades == 1  # 'open' excluded

    def test_equity_curve_and_drawdown_helpers(self):
        trades = [_trade(0.10), _trade(-0.05), _trade(0.20), _trade(-0.10), _trade(0.05)]
        equity = compute_equity_curve(trades)
        assert equity[0] == pytest.approx(1.0)
        assert len(equity) == 6
        assert max_drawdown_from_equity(equity) == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Empty / insufficient data safety
# ---------------------------------------------------------------------------

class TestEmptyAndInsufficient:
    def test_empty_price_data(self):
        result = Backtester().run({}, _run_config())
        assert result.trades == []
        assert result.metrics.total_trades == 0
        assert result.metrics.win_rate == 0.0
        assert result.metrics.profit_factor == 0.0
        assert result.metrics.sharpe == 0.0
        assert result.metrics.max_drawdown_pct == 0.0
        assert result.metrics.expectancy == 0.0
        assert result.equity_curve == [1.0]
        assert result.period_start is None

    def test_insufficient_bars_skipped(self):
        short = _breakout_series(seed=1, outcome="win").iloc[:30]  # < warmup_bars
        result = Backtester().run({"S": short}, _run_config(warmup_bars=49))
        assert result.metrics.total_trades == 0
        assert result.trades == []

    def test_metrics_from_empty_list_is_safe(self):
        m = BacktestMetrics.from_trades([])
        assert m.total_trades == 0
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
        assert m.max_drawdown_pct == 0.0

    def test_none_and_missing_columns_skipped(self):
        bad = pd.DataFrame({"Close": [1, 2, 3]})  # missing OHLCV columns
        result = Backtester().run({"BAD": bad, "NONE": None}, _run_config())
        assert result.metrics.total_trades == 0


# ---------------------------------------------------------------------------
# Walk-forward (BT-04)
# ---------------------------------------------------------------------------

class TestWalkForward:
    def test_returns_expected_number_of_splits(self):
        cfg = BacktestConfig(min_rr=1.2, warmup_bars=60, max_holding_days=15, volume_profile_bins=15)
        splits = Backtester().walk_forward({"X": _long_series()}, cfg, n_splits=3, train_frac=0.6)
        assert len(splits) == 3
        assert all(isinstance(s, WalkForwardSplit) for s in splits)

    def test_train_test_ranges_are_configurable_and_sequential(self):
        cfg = BacktestConfig(min_rr=1.2, warmup_bars=60, max_holding_days=15, volume_profile_bins=15)
        data = {"X": _long_series()}

        splits = Backtester().walk_forward(data, cfg, n_splits=3, train_frac=0.6)
        for s in splits:
            # train precedes test; test window is the out-of-sample tail
            assert s.train_start < s.train_end == s.test_start < s.test_end
        # splits are sequential and non-overlapping in their span
        for a, b in zip(splits, splits[1:]):
            assert b.train_start == a.test_end

        # a different train_frac / n_splits is honoured
        splits2 = Backtester().walk_forward(data, cfg, n_splits=2, train_frac=0.5)
        assert len(splits2) == 2
        span = splits2[0].test_end - splits2[0].train_start
        train_len = splits2[0].train_end - splits2[0].train_start
        assert train_len == pytest.approx(span / 2, abs=pd.Timedelta(days=1).value)

    def test_signals_restricted_to_test_window(self):
        cfg = BacktestConfig(min_rr=1.2, warmup_bars=60, max_holding_days=15, volume_profile_bins=15)
        splits = Backtester().walk_forward({"X": _long_series()}, cfg, n_splits=3, train_frac=0.6)
        for s in splits:
            for t in s.result.trades:
                assert s.test_start <= t.entry_date <= s.test_end


# ---------------------------------------------------------------------------
# Optimize (BT-05)
# ---------------------------------------------------------------------------

class TestOptimize:
    def test_sweep_grid_and_sort_by_expectancy(self):
        price_data = {f"W{i}": _breakout_series(seed=i, outcome="win") for i in range(3)}
        price_data.update({f"L{i}": _breakout_series(seed=100 + i, outcome="loss") for i in range(2)})

        grid = {
            "min_rr": [1.2, 1.5],
            "consolidation_range_pct": [0.06, 0.08],
            "volume_spike_multiplier": [1.5],
        }
        results = Backtester().optimize(price_data, grid, _run_config())

        assert len(results) == 2 * 2 * 1  # cartesian product
        for r in results:
            assert set(r["params"].keys()) == {"min_rr", "consolidation_range_pct", "volume_spike_multiplier"}
            assert "metrics" in r
        # sorted by expectancy descending
        expectancies = [r["metrics"]["expectancy"] for r in results]
        assert expectancies == sorted(expectancies, reverse=True)

    def test_empty_grid_runs_base_config(self):
        results = Backtester().optimize({"W": _breakout_series(seed=3)}, {}, _run_config())
        assert len(results) == 1
        assert results[0]["params"] == {}


# ---------------------------------------------------------------------------
# Report generation (BT-06)
# ---------------------------------------------------------------------------

class TestReport:
    def _result(self):
        price_data = {f"W{i}": _breakout_series(seed=i, outcome="win") for i in range(3)}
        price_data.update({f"L{i}": _breakout_series(seed=100 + i, outcome="loss") for i in range(2)})
        return Backtester().run(price_data, _run_config())

    def test_report_written_with_pass_fail_per_threshold(self, tmp_path):
        result = self._result()
        path = generate_report(result, out_dir=tmp_path, filename="report.md")

        assert path.exists()
        text = path.read_text(encoding="utf-8")

        # every PRD threshold criterion has an explicit PASS or FAIL
        for label in ("Expectancy", "Win rate", "Sharpe", "Max drawdown"):
            matching = [ln for ln in text.splitlines() if label in ln and ("PASS" in ln or "FAIL" in ln)]
            assert matching, f"no PASS/FAIL line for {label}"

        assert "PASS" in text or "FAIL" in text
        assert "# Backtest Report" in text

    def test_thresholds_are_overridable(self, tmp_path):
        result = self._result()
        # impossible drawdown threshold -> forces a FAIL on that criterion
        checks = evaluate_thresholds(result.metrics, {"max_drawdown_max": -1.0})
        dd = [c for c in checks if c[0] == "Max drawdown"][0]
        assert dd[1] is False

    def test_default_thresholds_evaluate_all_four(self):
        result = self._result()
        checks = evaluate_thresholds(result.metrics)
        labels = {c[0] for c in checks}
        assert labels == {"Expectancy", "Win rate", "Sharpe", "Max drawdown"}
