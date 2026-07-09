"""
Outcome analytics for the Learning Agent (PRD Wave 7, LEARN-01).

Pure, deterministic, dependency-light aggregation over *closed* trades. Given a
list of trade dicts (the shape returned by
:meth:`modules.journal.TradeJournal.get_closed_trades`) it computes headline
performance statistics plus per-pattern and per-sector breakdowns.

This module imports nothing heavy (no CrewAI / LangChain / network) so it can be
imported and unit-tested in a bare environment.

Expectancy / percentage convention
-----------------------------------
Every ``*_pct`` statistic and ``expectancy`` is expressed in the SAME units as
the input ``pnl_percent`` field. :class:`~modules.journal.TradeJournal` stores
``pnl_percent`` in **percent** (e.g. ``10.0`` == +10%), so with journal-sourced
trades ``expectancy`` is a *mean percent* value (``2.5`` == +2.5% per trade),
NOT a fraction. This differs deliberately from
:class:`modules.backtest.BacktestMetrics`, whose ``expectancy`` is a fraction --
the two come from different sources and are documented independently.

``max_drawdown_pct`` is a positive *fraction* (``0.19`` == 19%), computed from a
sequential equity curve that compounds ``(1 + pnl_percent / 100)`` in the order
trades are supplied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Outcome labels treated as authoritative when present on a trade dict.
_WIN = "win"
_LOSS = "loss"
_BREAKEVEN = "breakeven"


# ---------------------------------------------------------------------------
# Small, defensive field accessors
# ---------------------------------------------------------------------------

def _f(value: Any, default: float = 0.0) -> float:
    """Coerce to float, tolerating None / bad values."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pnl_percent(trade: Dict[str, Any]) -> float:
    return _f(trade.get("pnl_percent"))


def _pnl(trade: Dict[str, Any]) -> float:
    return _f(trade.get("pnl"))


def _pattern(trade: Dict[str, Any]) -> str:
    value = trade.get("pattern")
    return str(value) if value else "unknown"


def _sector(trade: Dict[str, Any]) -> str:
    value = trade.get("sector")
    return str(value) if value else "Unknown"


def _classify(trade: Dict[str, Any]) -> str:
    """Return canonical outcome ('win'|'loss'|'breakeven') for a trade.

    Uses the explicit ``outcome`` field when it is a recognised label; otherwise
    falls back to the sign of ``pnl`` (then ``pnl_percent``).
    """
    outcome = trade.get("outcome")
    if isinstance(outcome, str) and outcome.lower() in (_WIN, _LOSS, _BREAKEVEN):
        return outcome.lower()
    basis = _pnl(trade)
    if basis == 0.0:
        basis = _pnl_percent(trade)
    if basis > 0:
        return _WIN
    if basis < 0:
        return _LOSS
    return _BREAKEVEN


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _max_drawdown_pct(returns_percent: List[float]) -> float:
    """Max peak-to-trough decline (positive fraction) of a compounded curve.

    ``returns_percent`` are per-trade returns in percent; equity starts at 1.0
    and compounds ``(1 + r / 100)`` in the order supplied.
    """
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns_percent:
        equity *= 1.0 + (r / 100.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _group_stats(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute the {trades, win_rate, expectancy} summary for a group."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "expectancy": 0.0}
    wins = sum(1 for t in trades if _classify(t) == _WIN)
    returns = [_pnl_percent(t) for t in trades]
    return {
        "trades": n,
        "win_rate": round(wins / n, 6),
        "expectancy": round(_mean(returns), 6),
    }


# ---------------------------------------------------------------------------
# Stats dataclass
# ---------------------------------------------------------------------------

@dataclass
class LearningStats:
    """Aggregate performance statistics over a set of closed trades.

    ``expectancy`` / ``avg_win_pct`` / ``avg_loss_pct`` share the units of the
    input ``pnl_percent`` (percent, when sourced from the journal).
    ``max_drawdown_pct`` is a positive fraction (0.19 == 19%).
    ``by_pattern`` / ``by_sector`` map a label to ``{trades, win_rate,
    expectancy}``.
    """

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0        # negative or zero
    expectancy: float = 0.0          # mean pnl_percent (percent units)
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0    # positive fraction
    avg_pnl: float = 0.0             # mean absolute pnl (currency)
    by_pattern: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_sector: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public analytics API
# ---------------------------------------------------------------------------

def analyze_trades(trades: List[Dict[str, Any]]) -> LearningStats:
    """Compute :class:`LearningStats` from a list of closed-trade dicts.

    Empty input yields safe zeros (no division by zero). Missing fields are
    tolerated via ``.get`` fallbacks.
    """
    trades = list(trades or [])
    total = len(trades)
    if total == 0:
        return LearningStats()

    classes = [_classify(t) for t in trades]
    winners = [t for t, c in zip(trades, classes) if c == _WIN]
    losers = [t for t, c in zip(trades, classes) if c == _LOSS]

    wins = len(winners)
    losses = len(losers)

    all_returns = [_pnl_percent(t) for t in trades]
    win_returns = [_pnl_percent(t) for t in winners]
    loss_returns = [_pnl_percent(t) for t in losers]

    gross_profit = sum(win_returns)
    gross_loss = abs(sum(loss_returns))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    # Per-group breakdowns.
    pattern_groups: Dict[str, List[Dict[str, Any]]] = {}
    sector_groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        pattern_groups.setdefault(_pattern(t), []).append(t)
        sector_groups.setdefault(_sector(t), []).append(t)

    return LearningStats(
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=round(wins / total, 6),
        avg_win_pct=round(_mean(win_returns), 6),
        avg_loss_pct=round(_mean(loss_returns), 6),
        expectancy=round(_mean(all_returns), 6),
        profit_factor=(
            profit_factor if profit_factor == float("inf") else round(profit_factor, 6)
        ),
        max_drawdown_pct=round(_max_drawdown_pct(all_returns), 6),
        avg_pnl=round(_mean([_pnl(t) for t in trades]), 6),
        by_pattern={k: _group_stats(v) for k, v in pattern_groups.items()},
        by_sector={k: _group_stats(v) for k, v in sector_groups.items()},
    )


def analyze_journal(journal: Any) -> LearningStats:
    """Analyse the closed trades held by a :class:`TradeJournal`-like object."""
    trades = journal.get_closed_trades() if journal is not None else []
    return analyze_trades(trades)
