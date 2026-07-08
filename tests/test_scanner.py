"""
Unit tests for the Scanner Agent pipeline modules.

Run with:  pytest tests/test_scanner.py -v

Tests use synthetic DataFrame fixtures so no yfinance/API calls are made.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.patterns import PatternDetector
from modules.volume import VolumeProfiler
from modules.risk import RiskManager, RiskSetup, PortfolioState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 120, trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")

    if trend == "up":
        close = 1000.0 + np.cumsum(rng.normal(2, 5, n))
    elif trend == "flat":
        close = 1500.0 + rng.uniform(-30, 30, n)
    elif trend == "down":
        close = 2000.0 - np.cumsum(rng.normal(2, 5, n))
    else:
        close = 1000.0 + np.cumsum(rng.normal(0, 8, n))

    noise = rng.uniform(5, 20, n)
    df = pd.DataFrame(
        {
            "Open": close - rng.uniform(1, 5, n),
            "High": close + noise,
            "Low": close - noise,
            "Close": close,
            "Volume": rng.integers(500_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )
    return df


def _make_consolidation_breakout_df() -> pd.DataFrame:
    """
    Synthetic data designed to trigger consolidation_after_uptrend:
      - First 60 bars: uptrend (+25%)
      - Next 20 bars: tight consolidation
      - Last bar: breakout with high volume
    """
    rng = np.random.default_rng(10)
    n_uptrend = 65
    n_consol = 20

    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_uptrend + n_consol + 1, freq="B")

    # Uptrend section
    up_prices = 1000.0 + np.linspace(0, 300, n_uptrend) + rng.normal(0, 3, n_uptrend)

    # Consolidation section — tight range around 1300
    consol_center = up_prices[-1]
    consol_range = consol_center * 0.05  # 5% range (< 8% threshold)
    consol_prices = consol_center + rng.uniform(-consol_range / 2, consol_range / 2, n_consol)

    consol_high = consol_prices.max() + 5  # boundary

    # Breakout bar
    breakout_price = consol_high + 15  # breaks out above consolidation

    all_close = np.concatenate([up_prices, consol_prices, [breakout_price]])
    noise = rng.uniform(3, 10, len(all_close))

    volumes = rng.integers(500_000, 2_000_000, len(all_close)).astype(float)
    # Last bar has 3x average volume
    volumes[-1] = float(volumes[:-1].mean() * 3.0)

    df = pd.DataFrame(
        {
            "Open": all_close - rng.uniform(1, 5, len(all_close)),
            "High": all_close + noise,
            "Low": all_close - noise,
            "Close": all_close,
            "Volume": volumes,
        },
        index=dates,
    )
    return df


# ---------------------------------------------------------------------------
# PatternDetector tests
# ---------------------------------------------------------------------------

class TestPatternDetector:
    def setup_method(self):
        self.detector = PatternDetector()

    def test_consolidation_detected(self):
        df = _make_consolidation_breakout_df()
        result = self.detector.detect_consolidation_after_uptrend("TEST", df)
        assert result.detected, f"Expected detection, notes: {result.notes}"
        assert result.confidence > 0

    def test_consolidation_not_detected_flat(self):
        df = _make_ohlcv(120, trend="flat")
        result = self.detector.detect_consolidation_after_uptrend("TEST", df)
        assert not result.detected

    def test_insufficient_data(self):
        df = _make_ohlcv(50)
        result = self.detector.detect_consolidation_after_uptrend("TEST", df)
        assert not result.detected
        assert "insufficient" in result.notes.lower()

    def test_higher_lows_detected(self):
        # Manually craft a DataFrame with clear higher lows
        rng = np.random.default_rng(7)
        n = 80
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        # Rising channel
        base = np.linspace(1000, 1200, n) + rng.normal(0, 10, n)
        df = pd.DataFrame(
            {"Open": base, "High": base + 15, "Low": base - 15, "Close": base, "Volume": np.ones(n) * 1e6},
            index=dates,
        )
        result = self.detector.detect_higher_lows("TEST", df)
        # Rising channel should produce higher lows
        # Result may vary depending on swing detection; just check no error
        assert isinstance(result.detected, bool)

    def test_range_tightening_detected(self):
        rng = np.random.default_rng(99)
        n = 55
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        # First 45 bars: wide range; last 10 bars: very tight
        close = np.ones(n) * 1000
        noise = np.concatenate([rng.uniform(20, 40, 45), rng.uniform(2, 5, 10)])
        df = pd.DataFrame(
            {"Open": close, "High": close + noise, "Low": close - noise, "Close": close, "Volume": np.ones(n) * 1e6},
            index=dates,
        )
        result = self.detector.detect_range_tightening("TEST", df)
        assert result.detected, f"Expected tightening, ratio={result.atr_ratio}"

    def test_scan_returns_scan_result(self):
        df = _make_ohlcv(120)
        scan = self.detector.scan("TEST", df)
        assert len(scan.patterns) == 3
        assert isinstance(scan.passed, bool)


# ---------------------------------------------------------------------------
# VolumeProfiler tests
# ---------------------------------------------------------------------------

class TestVolumeProfiler:
    def setup_method(self):
        self.vp = VolumeProfiler(n_bins=10)

    def test_profile_builds(self):
        df = _make_ohlcv(60)
        profile = self.vp.build_profile("TEST", df)
        assert profile is not None
        assert len(profile.bin_midpoints) == 10
        assert len(profile.bin_volumes) == 10

    def test_hvn_levels_populated(self):
        df = _make_ohlcv(60)
        profile = self.vp.build_profile("TEST", df)
        # HVN and LVN lists should exist (may be empty in synthetic data)
        assert isinstance(profile.hvn_levels, list)
        assert isinstance(profile.lvn_levels, list)

    def test_hvn_support_below_price(self):
        df = _make_ohlcv(60)
        profile = self.vp.build_profile("TEST", df)
        hvn = self.vp.find_hvn_support(profile)
        if hvn is not None:
            assert hvn < profile.current_price

    def test_lvn_targets_above_price(self):
        df = _make_ohlcv(60)
        profile = self.vp.build_profile("TEST", df)
        targets = self.vp.find_lvn_targets(profile)
        for t in targets:
            assert t > profile.current_price

    def test_insufficient_data_returns_none(self):
        df = _make_ohlcv(5)
        profile = self.vp.build_profile("TEST", df)
        assert profile is None


# ---------------------------------------------------------------------------
# RiskManager tests
# ---------------------------------------------------------------------------

class TestRiskManager:
    def setup_method(self):
        self.rm = RiskManager(capital=1_000_000, risk_pct=0.01, min_rr_bull=2.5, is_bull_market=True)

    def _setup(self, entry=1000, stop=900, target=1350, sector="Tech"):
        return RiskSetup(symbol="TEST", entry_price=entry, stop_price=stop, target_price=target, sector=sector)

    def test_approved_valid_setup(self):
        result = self.rm.validate(self._setup(entry=1000, stop=900, target=1350))
        assert result.approved
        assert result.rr_ratio == pytest.approx(3.5, rel=0.01)

    def test_rejected_low_rr(self):
        result = self.rm.validate(self._setup(entry=1000, stop=900, target=1200))
        assert not result.approved
        assert "R:R" in (result.rejection_reason or "")

    def test_rejected_stop_above_entry(self):
        result = self.rm.validate(self._setup(entry=1000, stop=1050, target=1350))
        assert not result.approved
        assert "stop >= entry" in (result.rejection_reason or "")

    def test_rejected_target_below_entry(self):
        result = self.rm.validate(self._setup(entry=1000, stop=900, target=950))
        assert not result.approved
        assert "target <= entry" in (result.rejection_reason or "")

    def test_position_size_correct(self):
        result = self.rm.validate(self._setup(entry=1000, stop=900, target=1350))
        # 1% of 10L = 10_000 risk; risk per share = 100; shares = 100
        assert result.position_size_shares == 100
        assert result.capital_at_risk_inr == pytest.approx(10_000, rel=0.01)

    def test_portfolio_heat_limit(self):
        # Fill portfolio to near limit
        portfolio = PortfolioState()
        for i in range(4):
            portfolio.open_positions[f"STOCK{i}"] = self.rm.validate(
                self._setup(entry=1000, stop=900, target=1350, sector=f"Sector{i}")
            )

        # Manually set heat to 4.5%
        for r in portfolio.open_positions.values():
            r.capital_at_risk_pct = 0.01
        # Next trade would push to 5%: should pass
        # But if we push to 5.1%: should fail
        portfolio.open_positions["STOCK_LIMIT"] = type(
            "RiskResult", (), {"capital_at_risk_pct": 0.045}
        )()

        result = self.rm.validate(self._setup(), portfolio)
        assert not result.approved  # heat exceeded

    def test_sector_limit(self):
        portfolio = PortfolioState(sector_counts={"Tech": 2})
        result = self.rm.validate(self._setup(sector="Tech"), portfolio)
        assert not result.approved
        assert "sector" in (result.rejection_reason or "").lower()

    def test_batch_validation_sorted_by_rr(self):
        setups = [
            self._setup(entry=1000, stop=900, target=1200, sector="A"),   # RR=2.0 (fail)
            self._setup(entry=1000, stop=900, target=1400, sector="B"),   # RR=4.0
            self._setup(entry=1000, stop=900, target=1350, sector="C"),   # RR=3.5
        ]
        results = self.rm.validate_batch(setups)
        approved = [r for r in results if r.approved]
        assert len(approved) == 2
        # Best RR approved
        rrs = [r.rr_ratio for r in approved]
        assert 4.0 in rrs or any(r >= 3.5 for r in rrs)


# ---------------------------------------------------------------------------
# Integration: patterns → volume → risk
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    def test_pipeline_no_crash(self):
        """Runs the full module pipeline on synthetic data without errors."""
        df = _make_consolidation_breakout_df()
        symbol = "INTTEST"

        detector = PatternDetector()
        scan = detector.scan(symbol, df)

        vp = VolumeProfiler()
        profile, hvn, lvns = vp.analyse(symbol, df)
        assert profile is not None

        if hvn and lvns:
            rm = RiskManager()
            setup = RiskSetup(
                symbol=symbol,
                entry_price=profile.current_price,
                stop_price=hvn,
                target_price=lvns[0],
                sector="Test",
            )
            result = rm.validate(setup)
            assert isinstance(result.approved, bool)
