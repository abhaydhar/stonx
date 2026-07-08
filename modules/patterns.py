"""
Technical Pattern Detection Module
Detects three breakout patterns on daily OHLCV data:

  1. consolidation_after_uptrend
     Prior uptrend (≥20 % gain over 60 days) followed by tight range
     (≤8 % over last 20 bars) with current close breaking above the
     consolidation high on above-average volume.

  2. higher_lows
     At least 3 higher swing lows within the last 60 bars.  Current
     price holds above the most recent swing low.

  3. range_tightening (compression)
     Average true range of the last 10 bars < 70 % of the average
     true range of the prior 20 bars — range compression before a
     potential expansion move.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PatternResult:
    """Result of a single pattern check on one stock."""
    symbol: str
    pattern_name: str           # 'consolidation_after_uptrend' | 'higher_lows' | 'range_tightening'
    detected: bool
    confidence: float           # 0–1 heuristic
    entry_price: Optional[float] = None
    consolidation_high: Optional[float] = None
    consolidation_low: Optional[float] = None
    consolidation_range_pct: Optional[float] = None
    uptrend_gain_pct: Optional[float] = None
    volume_spike_ratio: Optional[float] = None
    swing_lows: List[float] = field(default_factory=list)
    atr_ratio: Optional[float] = None        # ATR10 / ATR30 (range_tightening)
    notes: str = ""


@dataclass
class ScanResult:
    """Aggregated pattern scan for one stock."""
    symbol: str
    patterns: List[PatternResult]
    best_pattern: Optional[str] = None       # highest-confidence detected pattern
    passed: bool = False                     # at least one pattern detected


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range series."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def _swing_lows(close: pd.Series, order: int = 3) -> List[Tuple[int, float]]:
    """
    Detect swing lows: a bar is a swing low if it is lower than the
    `order` bars on each side.

    Returns list of (index_position, price) tuples.
    """
    lows: List[Tuple[int, float]] = []
    prices = close.values
    for i in range(order, len(prices) - order):
        window_left = prices[i - order : i]
        window_right = prices[i + 1 : i + order + 1]
        if prices[i] < window_left.min() and prices[i] < window_right.min():
            lows.append((i, float(prices[i])))
    return lows


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

class PatternDetector:
    """
    Stateless pattern detector.  Each method receives a cleaned OHLCV
    DataFrame and returns a PatternResult.
    """

    def __init__(
        self,
        consolidation_days: int = 20,
        consolidation_range_pct: float = 0.08,
        uptrend_lookback_days: int = 60,
        uptrend_min_gain_pct: float = 0.20,
        volume_spike_multiplier: float = 1.5,
        atr_compression_ratio: float = 0.70,
    ):
        self.consolidation_days = consolidation_days
        self.consolidation_range_pct = consolidation_range_pct
        self.uptrend_lookback_days = uptrend_lookback_days
        self.uptrend_min_gain_pct = uptrend_min_gain_pct
        self.volume_spike_multiplier = volume_spike_multiplier
        self.atr_compression_ratio = atr_compression_ratio

    # ------------------------------------------------------------------
    # Pattern 1: Consolidation after uptrend
    # ------------------------------------------------------------------

    def detect_consolidation_after_uptrend(
        self, symbol: str, df: pd.DataFrame
    ) -> PatternResult:
        """
        Criteria:
          a) Prior uptrend: close[-(uptrend_lookback+consolidation_days)] to
             close[-consolidation_days] gained >= uptrend_min_gain_pct
          b) Consolidation: (high - low) / low <= consolidation_range_pct
             over last consolidation_days bars (excluding today)
          c) Breakout: today's close > consolidation_high
          d) Volume confirmation: today's volume >= volume_spike_multiplier
             * 20-day average volume
        """
        base = PatternResult(
            symbol=symbol,
            pattern_name="consolidation_after_uptrend",
            detected=False,
            confidence=0.0,
        )

        min_bars = self.uptrend_lookback_days + self.consolidation_days + 5
        if len(df) < min_bars:
            base.notes = f"insufficient data ({len(df)} bars, need {min_bars})"
            return base

        # ---- a) Uptrend check ----
        uptrend_start_idx = -(self.uptrend_lookback_days + self.consolidation_days)
        uptrend_end_idx = -self.consolidation_days
        price_start = df["Close"].iloc[uptrend_start_idx]
        price_at_consol_start = df["Close"].iloc[uptrend_end_idx]
        uptrend_gain = (price_at_consol_start - price_start) / price_start

        if uptrend_gain < self.uptrend_min_gain_pct:
            base.notes = (
                f"uptrend gain {uptrend_gain:.1%} < "
                f"required {self.uptrend_min_gain_pct:.1%}"
            )
            return base

        # ---- b) Consolidation check ----
        consol_window = df.iloc[-self.consolidation_days - 1 : -1]  # excludes today
        consol_high = consol_window["High"].max()
        consol_low = consol_window["Low"].min()
        range_pct = (consol_high - consol_low) / consol_low

        if range_pct > self.consolidation_range_pct:
            base.uptrend_gain_pct = uptrend_gain * 100
            base.consolidation_range_pct = range_pct * 100
            base.notes = (
                f"range {range_pct:.1%} > allowed {self.consolidation_range_pct:.1%}"
            )
            return base

        # ---- c) Breakout check ----
        current_close = df["Close"].iloc[-1]
        if current_close <= consol_high:
            base.uptrend_gain_pct = uptrend_gain * 100
            base.consolidation_high = consol_high
            base.consolidation_range_pct = range_pct * 100
            base.notes = (
                f"no breakout: close {current_close:.2f} <= "
                f"consol high {consol_high:.2f}"
            )
            return base

        # ---- d) Volume confirmation ----
        vol_20d_avg = df["Volume"].iloc[-21:-1].mean()
        today_vol = df["Volume"].iloc[-1]
        vol_spike_ratio = today_vol / vol_20d_avg if vol_20d_avg > 0 else 0.0

        if vol_spike_ratio < self.volume_spike_multiplier:
            base.notes = (
                f"low volume: {vol_spike_ratio:.1f}x < "
                f"required {self.volume_spike_multiplier:.1f}x"
            )
            return base

        # ---- All conditions met ----
        confidence = min(
            1.0,
            0.4 * min(uptrend_gain / self.uptrend_min_gain_pct, 2.0)
            + 0.3 * (1 - range_pct / self.consolidation_range_pct)
            + 0.3 * min(vol_spike_ratio / self.volume_spike_multiplier, 2.0) / 2.0,
        )

        return PatternResult(
            symbol=symbol,
            pattern_name="consolidation_after_uptrend",
            detected=True,
            confidence=round(confidence, 3),
            entry_price=current_close,
            consolidation_high=consol_high,
            consolidation_low=consol_low,
            consolidation_range_pct=range_pct * 100,
            uptrend_gain_pct=uptrend_gain * 100,
            volume_spike_ratio=round(vol_spike_ratio, 2),
            notes=(
                f"uptrend +{uptrend_gain:.1%}, "
                f"range {range_pct:.1%}, "
                f"vol {vol_spike_ratio:.1f}x"
            ),
        )

    # ------------------------------------------------------------------
    # Pattern 2: Higher lows
    # ------------------------------------------------------------------

    def detect_higher_lows(
        self, symbol: str, df: pd.DataFrame
    ) -> PatternResult:
        """
        Criteria:
          a) Detect swing lows over last uptrend_lookback_days bars
          b) At least 3 consecutive swing lows where each is higher than the previous
          c) Current close > last detected swing low (still holding)
        """
        base = PatternResult(
            symbol=symbol,
            pattern_name="higher_lows",
            detected=False,
            confidence=0.0,
        )

        lookback = self.uptrend_lookback_days
        if len(df) < lookback + 10:
            base.notes = f"insufficient data ({len(df)} bars)"
            return base

        window = df.iloc[-lookback:]
        lows = _swing_lows(window["Close"], order=3)

        if len(lows) < 3:
            base.notes = f"only {len(lows)} swing lows detected (need ≥ 3)"
            return base

        # Check for sequence of higher lows
        prices_at_lows = [p for _, p in lows]
        consecutive_higher = 0
        for i in range(1, len(prices_at_lows)):
            if prices_at_lows[i] > prices_at_lows[i - 1]:
                consecutive_higher += 1
            else:
                consecutive_higher = 0  # reset on break

        if consecutive_higher < 2:  # need at least 2 steps up = 3 lows
            base.swing_lows = prices_at_lows
            base.notes = (
                f"only {consecutive_higher} consecutive higher lows "
                f"(need >= 2 steps)"
            )
            return base

        last_low = prices_at_lows[-1]
        current_close = df["Close"].iloc[-1]

        if current_close < last_low:
            base.notes = f"close {current_close:.2f} broke below last swing low {last_low:.2f}"
            return base

        confidence = min(1.0, 0.5 + 0.1 * (consecutive_higher - 2))

        return PatternResult(
            symbol=symbol,
            pattern_name="higher_lows",
            detected=True,
            confidence=round(confidence, 3),
            entry_price=current_close,
            swing_lows=prices_at_lows,
            notes=(
                f"{len(prices_at_lows)} swing lows found, "
                f"{consecutive_higher} consecutive rises"
            ),
        )

    # ------------------------------------------------------------------
    # Pattern 3: Range tightening (compression)
    # ------------------------------------------------------------------

    def detect_range_tightening(
        self, symbol: str, df: pd.DataFrame
    ) -> PatternResult:
        """
        Criteria:
          ATR(10) / ATR(30) <= atr_compression_ratio
          i.e. recent range is significantly tighter than prior range.
        """
        base = PatternResult(
            symbol=symbol,
            pattern_name="range_tightening",
            detected=False,
            confidence=0.0,
        )

        if len(df) < 40:
            base.notes = f"insufficient data ({len(df)} bars)"
            return base

        tr = _true_range(df)
        atr10 = tr.iloc[-10:].mean()
        atr30 = tr.iloc[-40:-10].mean()

        if atr30 == 0:
            base.notes = "ATR30 is zero"
            return base

        ratio = atr10 / atr30
        base.atr_ratio = round(ratio, 3)

        if ratio > self.atr_compression_ratio:
            base.notes = f"ATR ratio {ratio:.2f} > threshold {self.atr_compression_ratio:.2f}"
            return base

        current_close = df["Close"].iloc[-1]
        confidence = min(1.0, (self.atr_compression_ratio - ratio) / self.atr_compression_ratio + 0.3)

        return PatternResult(
            symbol=symbol,
            pattern_name="range_tightening",
            detected=True,
            confidence=round(confidence, 3),
            entry_price=current_close,
            atr_ratio=round(ratio, 3),
            notes=f"ATR10/ATR30 = {ratio:.2f} (compression detected)",
        )

    # ------------------------------------------------------------------
    # Unified scan
    # ------------------------------------------------------------------

    def scan(self, symbol: str, df: pd.DataFrame) -> ScanResult:
        """Run all three pattern detectors and return aggregated ScanResult."""
        results = [
            self.detect_consolidation_after_uptrend(symbol, df),
            self.detect_higher_lows(symbol, df),
            self.detect_range_tightening(symbol, df),
        ]

        detected = [r for r in results if r.detected]
        best = max(detected, key=lambda r: r.confidence) if detected else None

        scan = ScanResult(
            symbol=symbol,
            patterns=results,
            best_pattern=best.pattern_name if best else None,
            passed=bool(detected),
        )

        logger.info(
            f"[patterns] {symbol}: "
            + (f"PASS — {scan.best_pattern} (conf {best.confidence:.2f})" if scan.passed
               else "FAIL — no patterns detected")
        )
        return scan
