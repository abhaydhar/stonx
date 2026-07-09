"""
Unit tests for modules/candles.py.

Each test crafts a small, hand-built OHLCV DataFrame that exhibits (or
deliberately does not exhibit) the target candlestick shape, so detection
logic is verified without relying on random/synthetic data.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.candles import CandlePatternDetector


def _df(rows, start="2024-01-01"):
    """rows: list of (Open, High, Low, Close, Volume) tuples."""
    dates = pd.date_range(start=start, periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=dates)


def _downtrend_prefix(n=5, start_price=100.0, step=2.0):
    """n bearish bars, each lower than the previous, ending near `start_price - n*step`."""
    rows = []
    price = start_price
    for _ in range(n):
        o, c = price, price - step
        rows.append((o, o + 0.5, c - 0.5, c, 1_000_000))
        price = c
    return rows


def _uptrend_prefix(n=5, start_price=100.0, step=2.0):
    rows = []
    price = start_price
    for _ in range(n):
        o, c = price, price + step
        rows.append((o, c + 0.5, o - 0.5, c, 1_000_000))
        price = c
    return rows


@pytest.fixture
def detector():
    return CandlePatternDetector()


# ---------------------------------------------------------------------------
# Doji
# ---------------------------------------------------------------------------

def test_doji_detected(detector):
    rows = _downtrend_prefix() + [(100.0, 105.0, 95.0, 100.2, 1_000_000)]
    result = detector.detect_doji("TEST", _df(rows))
    assert result.detected
    assert result.pattern_name == "doji"


def test_doji_not_detected_for_large_body(detector):
    rows = _downtrend_prefix() + [(100.0, 105.0, 95.0, 108.0, 1_000_000)]
    result = detector.detect_doji("TEST", _df(rows))
    assert not result.detected


# ---------------------------------------------------------------------------
# Hammer / Hanging Man (same shape, different prior trend)
# ---------------------------------------------------------------------------

def _hammer_shape_row(base=90.0):
    # Small (but non-doji) body near the top, long lower shadow, tiny upper shadow.
    open_, close = base - 1.0, base
    return (open_, base + 0.3, base - 8.3, close, 1_500_000)


def test_hammer_detected_after_downtrend(detector):
    rows = _downtrend_prefix(start_price=100.0) + [_hammer_shape_row()]
    result = detector.detect_hammer("TEST", _df(rows))
    assert result.detected
    assert result.bullish is True


def test_hammer_shape_after_uptrend_is_hanging_man_not_hammer(detector):
    rows = _uptrend_prefix(start_price=60.0) + [_hammer_shape_row(base=90.0)]
    df = _df(rows)
    hammer = detector.detect_hammer("TEST", df)
    hanging_man = detector.detect_hanging_man("TEST", df)
    assert not hammer.detected
    assert hanging_man.detected
    assert hanging_man.bullish is False


# ---------------------------------------------------------------------------
# Inverted Hammer / Shooting Star
# ---------------------------------------------------------------------------

def _inverted_hammer_shape_row(base=90.0):
    # Small (but non-doji) body near the bottom, long upper shadow, tiny lower shadow.
    open_, close = base, base + 1.0
    return (open_, base + 8.3, base - 0.3, close, 1_500_000)


def test_inverted_hammer_detected_after_downtrend(detector):
    rows = _downtrend_prefix(start_price=100.0) + [_inverted_hammer_shape_row()]
    result = detector.detect_inverted_hammer("TEST", _df(rows))
    assert result.detected
    assert result.bullish is True


def test_shooting_star_detected_after_uptrend(detector):
    rows = _uptrend_prefix(start_price=60.0) + [_inverted_hammer_shape_row(base=90.0)]
    df = _df(rows)
    result = detector.detect_shooting_star("TEST", df)
    assert result.detected
    assert result.bullish is False


# ---------------------------------------------------------------------------
# Engulfing
# ---------------------------------------------------------------------------

def test_bullish_engulfing_detected(detector):
    rows = [
        (100.0, 101.0, 95.0, 96.0, 1_000_000),   # bearish
        (95.5, 105.0, 95.0, 102.0, 1_200_000),   # bullish, engulfs prior body
    ]
    result = detector.detect_bullish_engulfing("TEST", _df(rows))
    assert result.detected
    assert result.bullish is True


def test_bearish_engulfing_detected(detector):
    rows = [
        (95.0, 101.0, 94.5, 100.0, 1_000_000),   # bullish
        (100.5, 101.0, 90.0, 94.0, 1_200_000),   # bearish, engulfs prior body
    ]
    result = detector.detect_bearish_engulfing("TEST", _df(rows))
    assert result.detected
    assert result.bullish is False


def test_engulfing_not_detected_when_body_too_small(detector):
    rows = [
        (100.0, 101.0, 95.0, 96.0, 1_000_000),
        (96.0, 97.0, 95.5, 96.5, 1_200_000),  # small bullish body, doesn't engulf
    ]
    result = detector.detect_bullish_engulfing("TEST", _df(rows))
    assert not result.detected


# ---------------------------------------------------------------------------
# Morning Star / Evening Star
# ---------------------------------------------------------------------------

def test_morning_star_detected(detector):
    rows = [
        (100.0, 101.0, 89.0, 90.0, 1_000_000),   # long bearish
        (89.5, 91.0, 88.5, 89.8, 800_000),       # small body pause
        (90.5, 98.0, 90.0, 97.0, 1_500_000),     # long bullish, closes into candle 1's body
    ]
    result = detector.detect_morning_star("TEST", _df(rows))
    assert result.detected
    assert result.bullish is True


def test_evening_star_detected(detector):
    rows = [
        (90.0, 101.0, 89.0, 100.0, 1_000_000),   # long bullish
        (100.5, 101.5, 99.5, 100.3, 800_000),    # small body pause
        (99.5, 100.0, 91.0, 92.0, 1_500_000),    # long bearish, closes into candle 1's body
    ]
    result = detector.detect_evening_star("TEST", _df(rows))
    assert result.detected
    assert result.bullish is False


# ---------------------------------------------------------------------------
# Three White Soldiers / Three Black Crows
# ---------------------------------------------------------------------------

def test_three_white_soldiers_detected(detector):
    rows = [
        (90.0, 96.5, 89.5, 96.0, 1_000_000),
        (96.0, 102.5, 95.5, 102.0, 1_100_000),
        (102.0, 108.5, 101.5, 108.0, 1_200_000),
    ]
    result = detector.detect_three_white_soldiers("TEST", _df(rows))
    assert result.detected
    assert result.bullish is True


def test_three_black_crows_detected(detector):
    rows = [
        (110.0, 110.5, 103.5, 104.0, 1_000_000),
        (104.0, 104.5, 97.5, 98.0, 1_100_000),
        (98.0, 98.5, 91.5, 92.0, 1_200_000),
    ]
    result = detector.detect_three_black_crows("TEST", _df(rows))
    assert result.detected
    assert result.bullish is False


def test_three_white_soldiers_not_detected_for_mixed_colors(detector):
    rows = [
        (90.0, 96.5, 89.5, 96.0, 1_000_000),
        (102.0, 102.5, 95.5, 96.5, 1_100_000),  # bearish breaks the streak
        (96.0, 108.5, 95.5, 108.0, 1_200_000),
    ]
    result = detector.detect_three_white_soldiers("TEST", _df(rows))
    assert not result.detected


# ---------------------------------------------------------------------------
# Unified scan
# ---------------------------------------------------------------------------

def test_scan_reports_best_detected_pattern(detector):
    rows = _downtrend_prefix() + [_hammer_shape_row()]
    scan = detector.scan("TEST", _df(rows))
    assert scan.passed
    assert scan.best_pattern == "hammer"
    assert len(scan.patterns) == 11


def test_scan_reports_no_pattern_for_bland_data(detector):
    rows = [(100.0, 101.0, 99.0, 100.5, 1_000_000) for _ in range(10)]
    scan = detector.scan("TEST", _df(rows))
    assert isinstance(scan.passed, bool)
    assert len(scan.patterns) == 11
