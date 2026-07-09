"""
RISK-01 — Deterministic risk metrics from OHLCV price data.

Pure functions over pandas ``Series`` / ``DataFrame`` inputs. No network, no
LLM, no optional dependencies beyond pandas/numpy. Every function guards
against empty / too-short / degenerate input and returns a neutral ``0.0``
rather than raising, so the Risk Agent can compute metrics from whatever
fixture data it is handed.

DataFrame inputs are expected to carry ``High`` / ``Low`` / ``Close`` columns
(``Volume`` optional). Series inputs are treated as a close-price series. All
outputs are plain Python floats.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Union

import numpy as np
import pandas as pd

PriceInput = Union[pd.Series, pd.DataFrame, list, tuple, np.ndarray]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _as_series(data: Optional[PriceInput]) -> Optional[pd.Series]:
    """Coerce assorted price inputs to a clean float ``Series`` (or ``None``).

    A ``DataFrame`` is reduced to its ``Close`` column. Non-finite values are
    dropped. Returns ``None`` when nothing usable remains.
    """

    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        if "Close" not in data.columns:
            return None
        data = data["Close"]
    if isinstance(data, pd.Series):
        s = data.astype("float64")
    else:
        try:
            s = pd.Series(data, dtype="float64")
        except (ValueError, TypeError):
            return None
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    return s if not s.empty else None


def _returns(data: Optional[PriceInput]) -> pd.Series:
    """Simple percentage returns with non-finite values removed."""

    s = _as_series(data)
    if s is None or len(s) < 2:
        return pd.Series([], dtype="float64")
    rets = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    return rets


def _true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder True Range series from a High/Low/Close DataFrame."""

    high = df["High"].astype("float64")
    low = df["Low"].astype("float64")
    prev_close = df["Close"].astype("float64").shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def annualized_volatility(close: PriceInput, periods_per_year: int = 252) -> float:
    """Annualized volatility = stdev(daily simple returns) x sqrt(periods).

    Returns ``0.0`` when fewer than two returns are available.
    """

    rets = _returns(close)
    if len(rets) < 2:
        return 0.0
    daily_std = float(rets.std(ddof=1))
    if not math.isfinite(daily_std):
        return 0.0
    return daily_std * math.sqrt(max(periods_per_year, 0))


def beta(asset_close: PriceInput, benchmark_close: PriceInput) -> float:
    """Beta = cov(asset, benchmark) / var(benchmark) on aligned returns.

    Returns ``0.0`` if either leg is too short or the benchmark has no
    variance (degenerate).
    """

    a = _returns(asset_close)
    b = _returns(benchmark_close)
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a.iloc[-n:].reset_index(drop=True)
    b = b.iloc[-n:].reset_index(drop=True)
    var_b = float(b.var(ddof=1))
    if not math.isfinite(var_b) or var_b == 0.0:
        return 0.0
    cov = float(a.cov(b))
    if not math.isfinite(cov):
        return 0.0
    return cov / var_b


def correlation(a_close: PriceInput, b_close: PriceInput) -> float:
    """Pearson correlation of aligned returns; ``0.0`` if degenerate."""

    a = _returns(a_close)
    b = _returns(b_close)
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a.iloc[-n:].reset_index(drop=True)
    b = b.iloc[-n:].reset_index(drop=True)
    if float(a.std(ddof=1)) == 0.0 or float(b.std(ddof=1)) == 0.0:
        return 0.0
    corr = a.corr(b)
    if corr is None or not math.isfinite(corr):
        return 0.0
    return float(corr)


def average_true_range(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range over the last ``period`` bars (simple mean of TR).

    Returns ``0.0`` when the frame is missing columns or too short.
    """

    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 2:
        return 0.0
    if not {"High", "Low", "Close"}.issubset(df.columns):
        return 0.0
    tr = _true_range(df).replace([np.inf, -np.inf], np.nan).dropna()
    if tr.empty:
        return 0.0
    window = max(1, min(int(period), len(tr)))
    atr = float(tr.iloc[-window:].mean())
    return atr if math.isfinite(atr) else 0.0


def max_drawdown(close: PriceInput) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction in [0, 1]."""

    s = _as_series(close)
    if s is None or len(s) < 2:
        return 0.0
    running_max = s.cummax()
    # Avoid division by zero / non-positive peaks.
    valid = running_max > 0
    if not valid.any():
        return 0.0
    drawdown = (s[valid] - running_max[valid]) / running_max[valid]
    mdd = float(drawdown.min())
    if not math.isfinite(mdd):
        return 0.0
    return float(min(1.0, abs(mdd)))


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

@dataclass
class RiskMetrics:
    """Bundle of deterministic risk metrics for one symbol."""

    annualized_volatility: float = 0.0
    beta: float = 0.0
    correlation: float = 0.0
    atr: float = 0.0
    max_drawdown: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def compute_risk_metrics(
    df: pd.DataFrame,
    benchmark: Optional[pd.DataFrame] = None,
    period: int = 14,
    periods_per_year: int = 252,
) -> RiskMetrics:
    """Compute all risk metrics for ``df`` (optionally relative to a benchmark)."""

    close = _as_series(df)
    if close is None:
        return RiskMetrics()

    b = 0.0
    corr = 0.0
    bench_close = _as_series(benchmark)
    if bench_close is not None:
        b = beta(close, bench_close)
        corr = correlation(close, bench_close)

    return RiskMetrics(
        annualized_volatility=annualized_volatility(close, periods_per_year),
        beta=b,
        correlation=corr,
        atr=average_true_range(df, period) if isinstance(df, pd.DataFrame) else 0.0,
        max_drawdown=max_drawdown(close),
    )
