"""
Historical Backtesting Framework (PRD Wave 2: BT-01 .. BT-06)

A deterministic, event-driven backtester that reuses the *exact* scanner
strategy logic (pattern detection, volume-profile stop/target selection,
target-by-min-R:R) rather than reimplementing it.  Given a dict of
``{symbol: OHLCV DataFrame}`` it walks each series forward bar-by-bar,
opens a trade when the deterministic scanner signal fires, simulates the
trade to a target / stop / timeout exit, and aggregates trade statistics.

Reused, not duplicated (BT-02):
  * ``modules.patterns.PatternDetector``   – pattern detection
  * ``modules.volume.VolumeProfiler``      – HVN support / LVN targets
  * ``modules.scanner.DeterministicScanner._select_target``       – target-by-R:R
  * ``modules.scanner.DeterministicScanner._best_detected_pattern`` – best pattern
  * ``modules.scanner.build_pattern_detector_from_config`` / ``..._volume_profiler_from_config``

Public API
----------
Dataclasses:  ``BacktestConfig``, ``BacktestTrade``, ``BacktestMetrics``,
              ``BacktestResult``, ``WalkForwardSplit``
Engine:       ``Backtester.run`` / ``.walk_forward`` / ``.optimize``
Report:       ``generate_report``

Sharpe convention (documented):
  Sharpe is computed on the *per-trade* return series (``pnl_pct``) as
  ``mean / sample_std``  and annualised by ``sqrt(annualization_factor)``.
  The default ``annualization_factor`` is 252, i.e. each closed trade is
  treated like one daily return sample -- a deliberate simplification for a
  daily-bar swing system.  Fewer than two trades, or zero dispersion, yields
  a safe Sharpe of ``0.0``.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from modules.scanner import (
    DeterministicScanner,
    build_pattern_detector_from_config,
    build_volume_profiler_from_config,
    load_scanner_config,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """
    Backtest parameters.

    The first three fields (``min_rr``, ``consolidation_range_pct``,
    ``volume_spike_multiplier``) are the sweepable knobs used by ``optimize``.
    The remaining pattern / volume fields let the backtest build detectors
    that mirror the live scanner configuration.
    """

    # --- sweepable strategy knobs ---
    min_rr: float = 2.5
    consolidation_range_pct: float = 0.08
    volume_spike_multiplier: float = 1.5

    # --- simulation controls ---
    max_holding_days: int = 20
    warmup_bars: int = 120

    # --- capital / risk ---
    capital: float = 1_000_000.0
    risk_pct: float = 0.01

    # --- pattern-detector params (mirror ScannerConfig) ---
    consolidation_days: int = 20
    uptrend_lookback_days: int = 60
    uptrend_min_gain_pct: float = 0.20
    atr_compression_ratio: float = 0.70
    breakout_hold_bars: int = 2

    # --- volume-profile params ---
    volume_profile_bins: int = 20
    hvn_threshold: float = 1.5
    lvn_threshold: float = 0.5

    # --- metrics ---
    annualization_factor: float = 252.0

    @classmethod
    def from_scanner_config(cls, config: Optional[object] = None) -> "BacktestConfig":
        """Build a BacktestConfig from the project ScannerConfig defaults."""
        cfg = config if config is not None else load_scanner_config()

        def _get(name: str, default: Any) -> Any:
            return getattr(cfg, name, default)

        return cls(
            min_rr=float(_get("MIN_RR", _get("BULL_MARKET_MIN_RR", 2.5))),
            consolidation_range_pct=float(_get("CONSOLIDATION_RANGE_PCT", 0.08)),
            volume_spike_multiplier=float(_get("VOLUME_SPIKE_MULTIPLIER", 1.5)),
            capital=float(_get("CAPITAL", 1_000_000.0)),
            risk_pct=float(_get("RISK_PCT", 0.01)),
            consolidation_days=int(_get("CONSOLIDATION_DAYS", 20)),
            uptrend_lookback_days=int(_get("UPTREND_LOOKBACK_DAYS", 60)),
            uptrend_min_gain_pct=float(_get("UPTREND_MIN_GAIN_PCT", 0.20)),
            atr_compression_ratio=float(_get("ATR_COMPRESSION_RATIO", 0.70)),
            breakout_hold_bars=int(_get("BREAKOUT_HOLD_BARS", 2)),
            volume_profile_bins=int(_get("VOLUME_PROFILE_BINS", 20)),
            hvn_threshold=float(_get("HVN_THRESHOLD", 1.5)),
            lvn_threshold=float(_get("LVN_THRESHOLD", 0.5)),
        )

    def to_scanner_namespace(self) -> SimpleNamespace:
        """
        Expose config using the attribute names expected by the
        ``build_*_from_config`` helpers so we reuse the scanner builders.
        """
        return SimpleNamespace(
            CONSOLIDATION_DAYS=self.consolidation_days,
            CONSOLIDATION_RANGE_PCT=self.consolidation_range_pct,
            UPTREND_LOOKBACK_DAYS=self.uptrend_lookback_days,
            UPTREND_MIN_GAIN_PCT=self.uptrend_min_gain_pct,
            VOLUME_SPIKE_MULTIPLIER=self.volume_spike_multiplier,
            ATR_COMPRESSION_RATIO=self.atr_compression_ratio,
            BREAKOUT_HOLD_BARS=self.breakout_hold_bars,
            VOLUME_PROFILE_BINS=self.volume_profile_bins,
            HVN_THRESHOLD=self.hvn_threshold,
            LVN_THRESHOLD=self.lvn_threshold,
        )


# ---------------------------------------------------------------------------
# Trade / metrics dataclasses
# ---------------------------------------------------------------------------

# Outcomes that represent a *realised* (closed) trade counted in metrics.
CLOSED_OUTCOMES = ("win", "loss", "timeout")


@dataclass
class BacktestTrade:
    """One simulated trade."""

    symbol: str
    pattern: str
    entry_date: Any                 # pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_date: Any                  # pd.Timestamp
    exit: float
    pnl: float                      # per-share price delta (exit - entry)
    pnl_pct: float                  # fraction, (exit - entry) / entry
    rr_planned: float
    bars_held: int
    outcome: str                    # 'win' | 'loss' | 'timeout' | 'open'

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry_date"] = _iso(self.entry_date)
        d["exit_date"] = _iso(self.exit_date)
        return d


@dataclass
class BacktestMetrics:
    """Aggregate performance statistics over the closed trades."""

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0        # negative or zero
    expectancy: float = 0.0          # per-trade mean pnl_pct (fraction)
    profit_factor: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0    # positive fraction (0.2 == 20%)
    avg_bars_held: float = 0.0

    @classmethod
    def from_trades(
        cls,
        trades: List[BacktestTrade],
        annualization_factor: float = 252.0,
    ) -> "BacktestMetrics":
        """Compute metrics deterministically from a list of trades.

        Only trades whose outcome is a *closed* outcome (win/loss/timeout)
        with a numeric ``pnl_pct`` are counted. Empty input yields safe zeros.
        Equity curve for drawdown is built in the order trades are supplied.
        """
        closed = [
            t for t in trades
            if t.outcome in CLOSED_OUTCOMES and t.pnl_pct is not None
        ]
        total = len(closed)
        if total == 0:
            return cls()

        returns = [float(t.pnl_pct) for t in closed]
        win_returns = [r for r in returns if r > 0]
        loss_returns = [r for r in returns if r < 0]
        n_wins = len(win_returns)
        n_losses = len(loss_returns)

        win_rate = n_wins / total
        avg_win = float(np.mean(win_returns)) if win_returns else 0.0
        avg_loss = float(np.mean(loss_returns)) if loss_returns else 0.0
        expectancy = float(np.mean(returns))

        gross_profit = float(sum(win_returns))
        gross_loss = abs(float(sum(loss_returns)))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        if total >= 2:
            std = float(np.std(returns, ddof=1))
            sharpe = (
                (expectancy / std) * math.sqrt(annualization_factor)
                if std > 0
                else 0.0
            )
        else:
            sharpe = 0.0

        equity = compute_equity_curve(closed)
        max_dd = max_drawdown_from_equity(equity)
        avg_bars = float(np.mean([t.bars_held for t in closed]))

        return cls(
            total_trades=total,
            wins=n_wins,
            losses=n_losses,
            win_rate=round(win_rate, 6),
            avg_win_pct=round(avg_win, 6),
            avg_loss_pct=round(avg_loss, 6),
            expectancy=round(expectancy, 6),
            profit_factor=(
                profit_factor
                if math.isinf(profit_factor)
                else round(profit_factor, 6)
            ),
            sharpe=round(sharpe, 6),
            max_drawdown_pct=round(max_dd, 6),
            avg_bars_held=round(avg_bars, 4),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    """Full backtest output: config, period, trades, metrics, equity curve."""

    config: BacktestConfig
    period_start: Any               # pd.Timestamp | None
    period_end: Any                 # pd.Timestamp | None
    trades: List[BacktestTrade]
    metrics: BacktestMetrics
    equity_curve: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": asdict(self.config),
            "period": {
                "start": _iso(self.period_start),
                "end": _iso(self.period_end),
            },
            "trades": [t.to_dict() for t in self.trades],
            "metrics": self.metrics.to_dict(),
            "equity_curve": list(self.equity_curve),
        }


@dataclass
class WalkForwardSplit:
    """One walk-forward split: train window (context) + tested result."""

    index: int
    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    result: BacktestResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "train_start": _iso(self.train_start),
            "train_end": _iso(self.train_end),
            "test_start": _iso(self.test_start),
            "test_end": _iso(self.test_end),
            "result": self.result.to_dict(),
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, (pd.Timestamp, datetime)):
        return ts.isoformat()
    return str(ts)


def compute_equity_curve(
    trades: List[BacktestTrade],
    starting_equity: float = 1.0,
) -> List[float]:
    """Compound per-trade ``pnl_pct`` onto a starting equity of 1.0.

    Returns ``[starting_equity, e1, e2, ...]`` (one point per closed trade,
    in the order supplied). Only closed trades contribute.
    """
    equity = [float(starting_equity)]
    e = float(starting_equity)
    for t in trades:
        if t.outcome in CLOSED_OUTCOMES and t.pnl_pct is not None:
            e = e * (1.0 + float(t.pnl_pct))
            equity.append(e)
    return equity


def max_drawdown_from_equity(equity: List[float]) -> float:
    """Maximum peak-to-trough decline as a positive fraction (0.2 == 20%)."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class Backtester:
    """Deterministic, event-driven backtester."""

    # ------------------------------------------------------------------
    # Core run
    # ------------------------------------------------------------------

    def run(
        self,
        price_data: Dict[str, pd.DataFrame],
        config: BacktestConfig,
        signal_range: Optional[Tuple[Any, Any]] = None,
    ) -> BacktestResult:
        """Run the backtest over ``{symbol: OHLCV DataFrame}``.

        Args:
            price_data:   symbol -> DataFrame(Open/High/Low/Close/Volume),
                          DatetimeIndex ascending.
            config:       BacktestConfig.
            signal_range: optional ``(start, end)`` timestamps; only bars whose
                          date is within this (inclusive) range may *open* a
                          trade. Used by walk-forward to restrict signals to a
                          test window while still using earlier bars as warmup.

        Returns:
            BacktestResult with trades, metrics and equity curve.
        """
        ns = config.to_scanner_namespace()
        detector = build_pattern_detector_from_config(ns)
        profiler = build_volume_profiler_from_config(ns)

        sig_start, sig_end = (None, None)
        if signal_range is not None:
            sig_start = pd.Timestamp(signal_range[0]) if signal_range[0] is not None else None
            sig_end = pd.Timestamp(signal_range[1]) if signal_range[1] is not None else None

        trades: List[BacktestTrade] = []
        period_start: Optional[pd.Timestamp] = None
        period_end: Optional[pd.Timestamp] = None

        for symbol, raw_df in price_data.items():
            df = self._prepare(raw_df)
            if df is None or len(df) <= config.warmup_bars:
                continue

            first, last = df.index[0], df.index[-1]
            period_start = first if period_start is None else min(period_start, first)
            period_end = last if period_end is None else max(period_end, last)

            symbol_trades = self._run_symbol(
                symbol, df, detector, profiler, config, sig_start, sig_end
            )
            trades.extend(symbol_trades)

        # Aggregate in realisation (exit-date) order for a realistic equity curve.
        closed = [t for t in trades if t.outcome in CLOSED_OUTCOMES]
        closed_sorted = sorted(closed, key=lambda t: (t.exit_date, t.symbol))
        metrics = BacktestMetrics.from_trades(
            closed_sorted, annualization_factor=config.annualization_factor
        )
        equity_curve = compute_equity_curve(closed_sorted)

        logger.info(
            "[backtest] %d trades (%d closed) | expectancy %.4f | win_rate %.2f",
            len(trades), metrics.total_trades, metrics.expectancy, metrics.win_rate,
        )

        return BacktestResult(
            config=config,
            period_start=period_start,
            period_end=period_end,
            trades=trades,
            metrics=metrics,
            equity_curve=equity_curve,
        )

    # ------------------------------------------------------------------
    # Per-symbol walk-forward loop
    # ------------------------------------------------------------------

    def _run_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        detector,
        profiler,
        config: BacktestConfig,
        sig_start: Optional[pd.Timestamp],
        sig_end: Optional[pd.Timestamp],
    ) -> List[BacktestTrade]:
        trades: List[BacktestTrade] = []
        n = len(df)
        i = int(config.warmup_bars)

        while i < n:
            bar_date = df.index[i]

            # Restrict signal generation to the requested window (walk-forward).
            if sig_start is not None and bar_date < sig_start:
                i += 1
                continue
            if sig_end is not None and bar_date > sig_end:
                break

            window = df.iloc[: i + 1]

            scan = detector.scan(symbol, window)
            best = DeterministicScanner._best_detected_pattern(None, scan.patterns)
            if best is None:
                i += 1
                continue

            profile, hvn_support, lvn_targets = profiler.analyse(symbol, window)
            if profile is None or hvn_support is None or not lvn_targets:
                i += 1
                continue

            entry = float(df["Close"].iloc[i])
            stop = float(hvn_support)
            if stop >= entry:
                i += 1
                continue

            # Reuse the scanner's exact target-by-min-R:R selection logic.
            target = float(
                DeterministicScanner._select_target(
                    None, entry, stop, lvn_targets, config.min_rr
                )
            )
            risk = entry - stop
            reward = target - entry
            if risk <= 0 or reward <= 0:
                i += 1
                continue

            rr = reward / risk
            if rr < config.min_rr:
                i += 1
                continue

            trade, exit_idx = self._simulate_trade(
                symbol, best.pattern_name, df, i, entry, stop, target, rr, config
            )
            trades.append(trade)
            # Resume after the exit bar -> only one open position per symbol.
            i = exit_idx + 1

        return trades

    # ------------------------------------------------------------------
    # Forward trade simulation
    # ------------------------------------------------------------------

    def _simulate_trade(
        self,
        symbol: str,
        pattern: str,
        df: pd.DataFrame,
        entry_idx: int,
        entry: float,
        stop: float,
        target: float,
        rr: float,
        config: BacktestConfig,
    ) -> Tuple[BacktestTrade, int]:
        n = len(df)
        entry_date = df.index[entry_idx]
        last_holdable = entry_idx + int(config.max_holding_days)
        max_j = min(last_holdable, n - 1)

        for j in range(entry_idx + 1, max_j + 1):
            low = float(df["Low"].iloc[j])
            high = float(df["High"].iloc[j])
            # Conservative: if a bar spans both stop and target, assume the
            # stop was hit first (worst case for the strategy).
            if low <= stop:
                return (
                    self._make_trade(symbol, pattern, entry_date, df.index[j],
                                     entry, stop, target, stop, rr, j - entry_idx,
                                     "loss"),
                    j,
                )
            if high >= target:
                return (
                    self._make_trade(symbol, pattern, entry_date, df.index[j],
                                     entry, stop, target, target, rr, j - entry_idx,
                                     "win"),
                    j,
                )

        # No target/stop hit within the available window.
        exit_idx = max_j
        exit_price = float(df["Close"].iloc[exit_idx])
        # 'timeout' if we actually held the full max_holding_days; 'open' if the
        # data simply ran out before the max holding period elapsed.
        outcome = "timeout" if last_holdable <= n - 1 else "open"
        return (
            self._make_trade(symbol, pattern, entry_date, df.index[exit_idx],
                             entry, stop, target, exit_price, rr,
                             exit_idx - entry_idx, outcome),
            exit_idx,
        )

    @staticmethod
    def _make_trade(
        symbol: str,
        pattern: str,
        entry_date: Any,
        exit_date: Any,
        entry: float,
        stop: float,
        target: float,
        exit_price: float,
        rr: float,
        bars_held: int,
        outcome: str,
    ) -> BacktestTrade:
        pnl = exit_price - entry
        pnl_pct = (exit_price - entry) / entry if entry != 0 else 0.0
        return BacktestTrade(
            symbol=symbol,
            pattern=pattern,
            entry_date=entry_date,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            exit_date=exit_date,
            exit=round(exit_price, 4),
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 6),
            rr_planned=round(rr, 4),
            bars_held=int(bars_held),
            outcome=outcome,
        )

    @staticmethod
    def _prepare(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """Validate and normalise a price DataFrame (sorted, required cols)."""
        if df is None or len(df) == 0:
            return None
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(set(df.columns)):
            return None
        out = df.sort_index()
        return out

    # ------------------------------------------------------------------
    # Walk-forward (BT-04)
    # ------------------------------------------------------------------

    def walk_forward(
        self,
        price_data: Dict[str, pd.DataFrame],
        config: BacktestConfig,
        n_splits: int = 3,
        train_frac: float = 0.6,
    ) -> List[WalkForwardSplit]:
        """Split the overall date range into ``n_splits`` sequential windows.

        Each split reserves the first ``train_frac`` of its dates as the train
        (parameter-fitting) window and backtests the remaining out-of-sample
        test window.  Train bars are still fed to the detectors as warmup, but
        only test-window bars may open a trade.

        Returns one :class:`WalkForwardSplit` per split.
        """
        if n_splits < 1:
            raise ValueError("n_splits must be >= 1")
        if not (0.0 < train_frac < 1.0):
            raise ValueError("train_frac must be in (0, 1)")

        prepared = {
            sym: self._prepare(df)
            for sym, df in price_data.items()
        }
        prepared = {s: d for s, d in prepared.items() if d is not None and len(d) > 0}
        if not prepared:
            return []

        global_start = min(d.index[0] for d in prepared.values())
        global_end = max(d.index[-1] for d in prepared.values())
        total_span = global_end - global_start
        if total_span <= pd.Timedelta(0):
            return []

        split_span = total_span / n_splits
        splits: List[WalkForwardSplit] = []

        for k in range(n_splits):
            split_start = global_start + split_span * k
            split_end = global_start + split_span * (k + 1)
            train_end = split_start + split_span * train_frac
            test_start = train_end
            test_end = split_end

            # Slice each symbol to the split range (train provides warmup);
            # signals restricted to the test window via signal_range.
            sliced = {
                sym: d[(d.index >= split_start) & (d.index <= split_end)]
                for sym, d in prepared.items()
            }
            result = self.run(
                sliced, config, signal_range=(test_start, test_end)
            )
            splits.append(
                WalkForwardSplit(
                    index=k,
                    train_start=split_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    result=result,
                )
            )

        return splits

    # ------------------------------------------------------------------
    # Parameter optimisation (BT-05)
    # ------------------------------------------------------------------

    def optimize(
        self,
        price_data: Dict[str, pd.DataFrame],
        param_grid: Dict[str, List[Any]],
        base_config: BacktestConfig,
    ) -> List[Dict[str, Any]]:
        """Sweep a parameter grid and rank combos by expectancy (desc).

        ``param_grid`` maps :class:`BacktestConfig` field names (e.g.
        ``min_rr``, ``consolidation_range_pct``, ``volume_spike_multiplier``)
        to candidate value lists.  Returns a list of
        ``{"params": {...}, "metrics": {...}}`` sorted by expectancy desc.
        """
        if not param_grid:
            result = self.run(price_data, base_config)
            return [{"params": {}, "metrics": result.metrics.to_dict()}]

        keys = list(param_grid.keys())
        value_lists = [param_grid[k] for k in keys]

        results: List[Dict[str, Any]] = []
        for combo in itertools.product(*value_lists):
            overrides = dict(zip(keys, combo))
            cfg = replace(base_config, **overrides)
            res = self.run(price_data, cfg)
            results.append(
                {
                    "params": overrides,
                    "metrics": res.metrics.to_dict(),
                    "total_trades": res.metrics.total_trades,
                }
            )

        results.sort(key=lambda r: r["metrics"]["expectancy"], reverse=True)
        return results


# ---------------------------------------------------------------------------
# Report generation (BT-06)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "expectancy_min": 0.0,      # expectancy > 0
    "win_rate_min": 0.40,       # win_rate >= 0.40
    "sharpe_min": 1.0,          # sharpe >= 1.0
    "max_drawdown_max": 0.20,   # max_drawdown_pct <= 0.20
}


def _fmt(value: float) -> str:
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return f"{value:.4f}"


def evaluate_thresholds(
    metrics: BacktestMetrics,
    thresholds: Optional[Dict[str, float]] = None,
) -> List[Tuple[str, bool, str]]:
    """Return ``[(label, passed, detail), ...]`` for each PRD threshold."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    checks: List[Tuple[str, bool, str]] = []

    exp_ok = metrics.expectancy > th["expectancy_min"]
    checks.append((
        "Expectancy",
        exp_ok,
        f"{_fmt(metrics.expectancy)} (require > {_fmt(th['expectancy_min'])})",
    ))

    wr_ok = metrics.win_rate >= th["win_rate_min"]
    checks.append((
        "Win rate",
        wr_ok,
        f"{_fmt(metrics.win_rate)} (require >= {_fmt(th['win_rate_min'])})",
    ))

    sharpe_ok = metrics.sharpe >= th["sharpe_min"]
    checks.append((
        "Sharpe",
        sharpe_ok,
        f"{_fmt(metrics.sharpe)} (require >= {_fmt(th['sharpe_min'])})",
    ))

    dd_ok = metrics.max_drawdown_pct <= th["max_drawdown_max"]
    checks.append((
        "Max drawdown",
        dd_ok,
        f"{_fmt(metrics.max_drawdown_pct)} (require <= {_fmt(th['max_drawdown_max'])})",
    ))

    return checks


def generate_report(
    result: BacktestResult,
    thresholds: Optional[Dict[str, float]] = None,
    out_dir: str | Path = "reports",
    filename: Optional[str] = None,
) -> Path:
    """Write a Markdown backtest report with a PASS/FAIL line per threshold.

    Returns the path to the written report.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    if filename is None:
        filename = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path = directory / filename

    m = result.metrics
    checks = evaluate_thresholds(m, thresholds)
    overall_pass = all(passed for _, passed, _ in checks)

    period_start = _iso(result.period_start) or "n/a"
    period_end = _iso(result.period_end) or "n/a"

    lines: List[str] = []
    lines.append("# Backtest Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Period: {period_start} -> {period_end}")
    lines.append(f"- Overall: {'PASS' if overall_pass else 'FAIL'}")
    lines.append("")

    lines.append("## Configuration")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|---|---|")
    lines.append(f"| min_rr | {result.config.min_rr} |")
    lines.append(f"| consolidation_range_pct | {result.config.consolidation_range_pct} |")
    lines.append(f"| volume_spike_multiplier | {result.config.volume_spike_multiplier} |")
    lines.append(f"| max_holding_days | {result.config.max_holding_days} |")
    lines.append(f"| warmup_bars | {result.config.warmup_bars} |")
    lines.append(f"| capital | {result.config.capital} |")
    lines.append(f"| risk_pct | {result.config.risk_pct} |")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total trades | {m.total_trades} |")
    lines.append(f"| Wins | {m.wins} |")
    lines.append(f"| Losses | {m.losses} |")
    lines.append(f"| Win rate | {_fmt(m.win_rate)} |")
    lines.append(f"| Avg win % | {_fmt(m.avg_win_pct)} |")
    lines.append(f"| Avg loss % | {_fmt(m.avg_loss_pct)} |")
    lines.append(f"| Expectancy | {_fmt(m.expectancy)} |")
    lines.append(f"| Profit factor | {_fmt(m.profit_factor)} |")
    lines.append(f"| Sharpe | {_fmt(m.sharpe)} |")
    lines.append(f"| Max drawdown | {_fmt(m.max_drawdown_pct)} |")
    lines.append(f"| Avg bars held | {_fmt(m.avg_bars_held)} |")
    lines.append("")

    lines.append("## PRD Threshold Check")
    lines.append("")
    lines.append("| Criterion | Result | Detail |")
    lines.append("|---|---|---|")
    for label, passed, detail in checks:
        lines.append(f"| {label} | {'PASS' if passed else 'FAIL'} | {detail} |")
    lines.append("")

    lines.append("## Trades")
    lines.append("")
    if result.trades:
        lines.append("| Symbol | Pattern | Entry date | Entry | Stop | Target | Exit date | Exit | PnL % | R:R | Bars | Outcome |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for t in result.trades:
            lines.append(
                f"| {t.symbol} | {t.pattern} | {_iso(t.entry_date)} | {t.entry} | "
                f"{t.stop} | {t.target} | {_iso(t.exit_date)} | {t.exit} | "
                f"{_fmt(t.pnl_pct)} | {t.rr_planned} | {t.bars_held} | {t.outcome} |"
            )
    else:
        lines.append("_No trades generated._")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[backtest] report written to %s", path)
    return path
