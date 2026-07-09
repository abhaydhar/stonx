"""
Candlestick Pattern Detection Module

Pure pandas/numpy candle-anatomy rules for the last 1-3 bars of daily OHLCV
data (no TA-Lib dependency, consistent with this repo's TA-Lib-free policy —
see README "Dependency Notes"). Detects classic single/multi-candle reversal
patterns:

  Single-candle (shape depends on the short-term trend that precedes them):
    - doji              small body relative to the bar's range
    - hammer            small body near the top, long lower shadow, after a downtrend
    - hanging_man        same shape as hammer, but after an uptrend (bearish)
    - inverted_hammer    small body near the bottom, long upper shadow, after a downtrend
    - shooting_star      same shape as inverted_hammer, but after an uptrend (bearish)

  Two-candle:
    - bullish_engulfing  bullish body fully engulfs the prior bearish body
    - bearish_engulfing  bearish body fully engulfs the prior bullish body

  Three-candle:
    - morning_star       bearish, small-body/gap-down, bullish closing into candle 1
    - evening_star        bullish, small-body/gap-up, bearish closing into candle 1
    - three_white_soldiers three consecutive rising bullish candles with small shadows
    - three_black_crows    three consecutive falling bearish candles with small shadows
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CandlePatternResult:
    """Result of a single candlestick pattern check on one stock."""

    symbol: str
    pattern_name: str
    detected: bool
    bullish: bool
    confidence: float
    bar_date: Optional[str] = None
    notes: str = ""


@dataclass
class CandleScanResult:
    """Aggregated candlestick pattern scan for one stock."""

    symbol: str
    patterns: List[CandlePatternResult]
    best_pattern: Optional[str] = None
    passed: bool = False


# ---------------------------------------------------------------------------
# Candle-anatomy helpers (operate on a single OHLC row)
# ---------------------------------------------------------------------------

def _body(row: pd.Series) -> float:
    return abs(float(row["Close"]) - float(row["Open"]))


def _range(row: pd.Series) -> float:
    return float(row["High"]) - float(row["Low"])


def _upper_shadow(row: pd.Series) -> float:
    return float(row["High"]) - max(float(row["Open"]), float(row["Close"]))


def _lower_shadow(row: pd.Series) -> float:
    return min(float(row["Open"]), float(row["Close"])) - float(row["Low"])


def _is_bullish(row: pd.Series) -> bool:
    return float(row["Close"]) > float(row["Open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["Close"]) < float(row["Open"])


def _bar_date(df: pd.DataFrame, idx: int) -> Optional[str]:
    try:
        return df.index[idx].date().isoformat()
    except Exception:
        return None


class CandlePatternDetector:
    """
    Stateless candlestick pattern detector. Each method receives a cleaned
    OHLCV DataFrame and returns a CandlePatternResult for the most recent bar.
    """

    def __init__(
        self,
        doji_body_ratio: float = 0.10,
        shadow_ratio: float = 2.0,
        small_body_ratio: float = 0.30,
        trend_lookback: int = 5,
    ):
        self.doji_body_ratio = doji_body_ratio
        self.shadow_ratio = shadow_ratio
        self.small_body_ratio = small_body_ratio
        self.trend_lookback = max(2, trend_lookback)

    # ------------------------------------------------------------------
    # Trend context (used to disambiguate hammer/hanging-man, etc.)
    # ------------------------------------------------------------------

    def _prior_trend(self, df: pd.DataFrame, bars_before_last: int = 1) -> str:
        """Classify the trend of the `trend_lookback` bars preceding the pattern bar(s)."""

        end = -bars_before_last if bars_before_last else len(df)
        start = end - self.trend_lookback
        if abs(start) > len(df):
            return "flat"
        window = df["Close"].iloc[start:end]
        if len(window) < 2:
            return "flat"
        change = (window.iloc[-1] - window.iloc[0]) / window.iloc[0] if window.iloc[0] else 0.0
        if change > 0.01:
            return "up"
        if change < -0.01:
            return "down"
        return "flat"

    def _base_result(self, symbol: str, name: str) -> CandlePatternResult:
        return CandlePatternResult(symbol=symbol, pattern_name=name, detected=False, bullish=False, confidence=0.0)

    # ------------------------------------------------------------------
    # Single-candle patterns
    # ------------------------------------------------------------------

    def detect_doji(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        base = self._base_result(symbol, "doji")
        if len(df) < 1:
            base.notes = "insufficient data"
            return base

        row = df.iloc[-1]
        bar_range = _range(row)
        if bar_range <= 0:
            base.notes = "zero range bar"
            return base

        body_ratio = _body(row) / bar_range
        if body_ratio > self.doji_body_ratio:
            base.notes = f"body {body_ratio:.1%} of range > {self.doji_body_ratio:.0%}"
            return base

        confidence = min(1.0, 0.5 + (self.doji_body_ratio - body_ratio) / self.doji_body_ratio)
        return CandlePatternResult(
            symbol=symbol,
            pattern_name="doji",
            detected=True,
            bullish=False,
            confidence=round(confidence, 3),
            bar_date=_bar_date(df, -1),
            notes=f"body {body_ratio:.1%} of range (indecision)",
        )

    def _detect_hammer_shape(
        self,
        symbol: str,
        df: pd.DataFrame,
        name: str,
        want_lower_shadow: bool,
        required_trend: str,
        bullish: bool,
    ) -> CandlePatternResult:
        base = self._base_result(symbol, name)
        if len(df) < self.trend_lookback + 1:
            base.notes = "insufficient data"
            return base

        row = df.iloc[-1]
        bar_range = _range(row)
        if bar_range <= 0:
            base.notes = "zero range bar"
            return base

        body = _body(row)
        upper = _upper_shadow(row)
        lower = _lower_shadow(row)
        long_shadow = lower if want_lower_shadow else upper
        short_shadow = upper if want_lower_shadow else lower

        if body / bar_range > self.small_body_ratio:
            base.notes = f"body {body / bar_range:.1%} of range > {self.small_body_ratio:.0%}"
            return base
        if body == 0 or long_shadow < self.shadow_ratio * max(body, 1e-9):
            base.notes = f"long shadow {long_shadow:.2f} < {self.shadow_ratio:.1f}x body {body:.2f}"
            return base
        if short_shadow > body:
            base.notes = "opposite shadow too large"
            return base

        trend = self._prior_trend(df, bars_before_last=1)
        if trend != required_trend:
            base.notes = f"shape matches but prior trend is '{trend}', need '{required_trend}'"
            return base

        confidence = min(1.0, 0.5 + 0.1 * (long_shadow / max(body, 1e-9) - self.shadow_ratio))
        return CandlePatternResult(
            symbol=symbol,
            pattern_name=name,
            detected=True,
            bullish=bullish,
            confidence=round(max(0.5, confidence), 3),
            bar_date=_bar_date(df, -1),
            notes=f"shadow/body ratio {long_shadow / max(body, 1e-9):.1f}x after '{trend}' trend",
        )

    def detect_hammer(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_hammer_shape(symbol, df, "hammer", want_lower_shadow=True, required_trend="down", bullish=True)

    def detect_hanging_man(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_hammer_shape(symbol, df, "hanging_man", want_lower_shadow=True, required_trend="up", bullish=False)

    def detect_inverted_hammer(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_hammer_shape(symbol, df, "inverted_hammer", want_lower_shadow=False, required_trend="down", bullish=True)

    def detect_shooting_star(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_hammer_shape(symbol, df, "shooting_star", want_lower_shadow=False, required_trend="up", bullish=False)

    # ------------------------------------------------------------------
    # Two-candle patterns
    # ------------------------------------------------------------------

    def _detect_engulfing(self, symbol: str, df: pd.DataFrame, name: str, bullish: bool) -> CandlePatternResult:
        base = self._base_result(symbol, name)
        if len(df) < 2:
            base.notes = "insufficient data"
            return base

        prev, curr = df.iloc[-2], df.iloc[-1]
        prev_bullish, curr_bullish = _is_bullish(prev), _is_bullish(curr)

        if bullish:
            if not (_is_bearish(prev) and curr_bullish):
                base.notes = "candle colors don't match bullish engulfing"
                return base
            engulfs = float(curr["Open"]) <= float(prev["Close"]) and float(curr["Close"]) >= float(prev["Open"])
        else:
            if not (prev_bullish and _is_bearish(curr)):
                base.notes = "candle colors don't match bearish engulfing"
                return base
            engulfs = float(curr["Open"]) >= float(prev["Close"]) and float(curr["Close"]) <= float(prev["Open"])

        if not engulfs:
            base.notes = "current body does not fully engulf prior body"
            return base

        prev_body, curr_body = _body(prev), _body(curr)
        size_ratio = curr_body / prev_body if prev_body else 2.0
        confidence = min(1.0, 0.5 + 0.15 * min(size_ratio, 3.0))

        return CandlePatternResult(
            symbol=symbol,
            pattern_name=name,
            detected=True,
            bullish=bullish,
            confidence=round(confidence, 3),
            bar_date=_bar_date(df, -1),
            notes=f"body {size_ratio:.1f}x prior candle's body",
        )

    def detect_bullish_engulfing(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_engulfing(symbol, df, "bullish_engulfing", bullish=True)

    def detect_bearish_engulfing(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_engulfing(symbol, df, "bearish_engulfing", bullish=False)

    # ------------------------------------------------------------------
    # Three-candle patterns
    # ------------------------------------------------------------------

    def _detect_star(self, symbol: str, df: pd.DataFrame, name: str, bullish: bool) -> CandlePatternResult:
        base = self._base_result(symbol, name)
        if len(df) < 3:
            base.notes = "insufficient data"
            return base

        first, middle, last = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        first_range = _range(first)
        if first_range <= 0:
            base.notes = "zero range bar"
            return base

        if bullish:
            if not (_is_bearish(first) and _body(first) / first_range > self.small_body_ratio):
                base.notes = "first candle is not a long bearish body"
                return base
        else:
            if not (_is_bullish(first) and _body(first) / first_range > self.small_body_ratio):
                base.notes = "first candle is not a long bullish body"
                return base

        middle_range = _range(middle)
        if middle_range > 0 and _body(middle) / middle_range > self.small_body_ratio:
            base.notes = "middle candle body is not small"
            return base

        if bullish:
            if not (_is_bullish(last) and float(last["Close"]) > (float(first["Open"]) + float(first["Close"])) / 2):
                base.notes = "third candle does not close back into first candle's body"
                return base
        else:
            if not (_is_bearish(last) and float(last["Close"]) < (float(first["Open"]) + float(first["Close"])) / 2):
                base.notes = "third candle does not close back into first candle's body"
                return base

        confidence = 0.7
        return CandlePatternResult(
            symbol=symbol,
            pattern_name=name,
            detected=True,
            bullish=bullish,
            confidence=confidence,
            bar_date=_bar_date(df, -1),
            notes="three-candle reversal: long body, small-body pause, confirming reversal candle",
        )

    def detect_morning_star(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_star(symbol, df, "morning_star", bullish=True)

    def detect_evening_star(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_star(symbol, df, "evening_star", bullish=False)

    def _detect_three_in_a_row(self, symbol: str, df: pd.DataFrame, name: str, bullish: bool) -> CandlePatternResult:
        base = self._base_result(symbol, name)
        if len(df) < 3:
            base.notes = "insufficient data"
            return base

        bars = [df.iloc[-3], df.iloc[-2], df.iloc[-1]]
        color_check = all(_is_bullish(bar) for bar in bars) if bullish else all(_is_bearish(bar) for bar in bars)
        if not color_check:
            base.notes = "not three consecutive same-colored candles"
            return base

        closes = [float(bar["Close"]) for bar in bars]
        monotonic = all(closes[i] < closes[i + 1] for i in range(2)) if bullish else all(
            closes[i] > closes[i + 1] for i in range(2)
        )
        if not monotonic:
            base.notes = "closes are not strictly progressing in the same direction"
            return base

        for bar in bars:
            bar_range = _range(bar)
            if bar_range <= 0 or _body(bar) / bar_range < (1 - self.small_body_ratio):
                base.notes = "one or more candles have a small body / large shadows"
                return base

        return CandlePatternResult(
            symbol=symbol,
            pattern_name=name,
            detected=True,
            bullish=bullish,
            confidence=0.75,
            bar_date=_bar_date(df, -1),
            notes="three consecutive long same-direction candles",
        )

    def detect_three_white_soldiers(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_three_in_a_row(symbol, df, "three_white_soldiers", bullish=True)

    def detect_three_black_crows(self, symbol: str, df: pd.DataFrame) -> CandlePatternResult:
        return self._detect_three_in_a_row(symbol, df, "three_black_crows", bullish=False)

    # ------------------------------------------------------------------
    # Unified scan
    # ------------------------------------------------------------------

    def scan(self, symbol: str, df: pd.DataFrame) -> CandleScanResult:
        """Run every candlestick pattern detector and return the aggregated result."""

        results = [
            self.detect_doji(symbol, df),
            self.detect_hammer(symbol, df),
            self.detect_hanging_man(symbol, df),
            self.detect_inverted_hammer(symbol, df),
            self.detect_shooting_star(symbol, df),
            self.detect_bullish_engulfing(symbol, df),
            self.detect_bearish_engulfing(symbol, df),
            self.detect_morning_star(symbol, df),
            self.detect_evening_star(symbol, df),
            self.detect_three_white_soldiers(symbol, df),
            self.detect_three_black_crows(symbol, df),
        ]

        detected = [r for r in results if r.detected]
        best = max(detected, key=lambda r: r.confidence) if detected else None

        scan = CandleScanResult(
            symbol=symbol,
            patterns=results,
            best_pattern=best.pattern_name if best else None,
            passed=bool(detected),
        )
        logger.debug(
            "[candles] %s: %s",
            symbol,
            f"{scan.best_pattern} (conf {best.confidence:.2f})" if scan.passed else "no candle pattern",
        )
        return scan
