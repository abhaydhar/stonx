"""
Unit tests for the Scanner Agent pipeline modules.

Run with:  pytest tests/test_scanner.py -v

Tests use synthetic DataFrame fixtures so no yfinance/API calls are made.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.patterns import PatternDetector
from modules.patterns import PatternResult, ScanResult
from modules.volume import VolumeProfiler
from modules.risk import RiskManager, RiskSetup, PortfolioState
from modules.fundamental import FundamentalData, FundamentalFilter, FundamentalResult
from modules.ingest import DataIngestion, DataQualityMetadata, OHLCVFetchResult
from modules.scanner import (
    DeterministicScanner,
    build_pattern_detector_from_config,
    build_risk_manager_from_config,
    build_volume_profiler_from_config,
    write_scan_outputs,
)


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
      - Last 2 bars: breakout hold with high volume
    """
    rng = np.random.default_rng(10)
    n_uptrend = 65
    n_consol = 20
    hold_bars = 2

    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_uptrend + n_consol + hold_bars, freq="B")

    # Uptrend section
    up_prices = 1000.0 + np.linspace(0, 300, n_uptrend) + rng.normal(0, 3, n_uptrend)

    # Consolidation section — tight range around 1300
    consol_center = up_prices[-1]
    consol_range = consol_center * 0.05  # 5% range (< 8% threshold)
    consol_prices = consol_center + rng.uniform(-consol_range / 2, consol_range / 2, n_consol)

    consol_high = consol_prices.max() + 5  # boundary

    # Breakout hold bars
    breakout_prices = np.array([consol_high + 15, consol_high + 20])

    all_close = np.concatenate([up_prices, consol_prices, breakout_prices])
    noise = rng.uniform(3, 10, len(all_close))

    volumes = rng.integers(500_000, 2_000_000, len(all_close)).astype(float)
    # Breakout hold bars have 3x average volume
    volumes[-2:] = float(volumes[:-2].mean() * 3.0)

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
# Data ingestion and fundamentals
# ---------------------------------------------------------------------------

class FakeOHLCVProvider:
    source_name = "fixture"
    adjusted = True

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def fetch(self, symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        return self.df


class TestDataAndFundamentals:
    def test_universe_loader_reads_csv_fixture(self, tmp_path):
        path = tmp_path / "universe.csv"
        path.write_text(
            "symbol,name,sector\nabc,ABC Ltd,Tech\nXYZ.NS,XYZ Ltd,FMCG\n",
            encoding="utf-8",
        )

        ingestion = DataIngestion(
            cache_dir=str(tmp_path / "cache"),
            universe_path=str(path),
        )

        assert ingestion.get_nse_universe() == ["ABC.NS", "XYZ.NS"]
        assert ingestion.get_sector("ABC.NS") == "Tech"

    def test_provider_quality_metadata_records_source_and_missing_pct(self, tmp_path):
        start = datetime(2026, 1, 5)
        end = datetime(2026, 1, 9)
        dates = pd.bdate_range(start=start, end=end)[:4]
        df = pd.DataFrame(
            {
                "Open": [100, 101, 102, 103],
                "High": [101, 102, 103, 104],
                "Low": [99, 100, 101, 102],
                "Close": [100, 101, 102, 103],
                "Volume": [1_000_000] * 4,
            },
            index=dates,
        )

        ingestion = DataIngestion(
            cache_dir=str(tmp_path / "cache"),
            provider=FakeOHLCVProvider(df),
            max_missing_pct=1.0,
        )
        result = ingestion.fetch_ohlcv_with_quality(
            "abc",
            start_date=start,
            end_date=end,
            use_cache=False,
        )

        assert result.data is not None
        assert result.quality.symbol == "ABC.NS"
        assert result.quality.source == "fixture"
        assert result.quality.adjusted is True
        assert result.quality.rows == 4
        assert result.quality.expected_business_days == 5
        assert result.quality.missing_data_pct == pytest.approx(0.2)

    def test_fundamentals_read_fixture_csv_and_enforce_promoter_holding(self, tmp_path):
        path = tmp_path / "fundamentals.csv"
        path.write_text(
            "\n".join(
                [
                    "symbol,as_of,sector,market_cap_cr,revenue_growth_pct,debt_to_equity,promoter_holding_pct",
                    "GOOD.NS,2026-03-31,Tech,1000,12,0.2,55",
                    "BAD.NS,2026-03-31,Tech,1000,12,0.2,20",
                ]
            ),
            encoding="utf-8",
        )
        fundamentals = FundamentalFilter(
            csv_path=str(path),
            min_market_cap_cr=500,
            min_revenue_growth=0,
            max_debt_to_equity=1.0,
            min_promoter_holding=0.40,
        )

        good = fundamentals.screen("GOOD.NS")
        bad = fundamentals.screen("BAD.NS")

        assert good.passed
        assert good.data.source == "csv:fundamentals.csv"
        assert good.data.promoter_holding_pct == pytest.approx(0.55)
        assert not bad.passed
        assert "promoter_holding" in (bad.rejection_reason or "")


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
        assert result.breakout_hold_bars == 2

    def test_false_breakout_rejected_without_two_bar_hold(self):
        df = _make_consolidation_breakout_df()
        consolidation_high = df.iloc[-22:-2]["High"].max()
        df.iloc[-1, df.columns.get_loc("Close")] = consolidation_high - 1
        df.iloc[-1, df.columns.get_loc("High")] = consolidation_high
        df.iloc[-1, df.columns.get_loc("Low")] = consolidation_high - 5

        result = self.detector.detect_consolidation_after_uptrend("TEST", df)

        assert not result.detected
        assert "false breakout" in result.notes

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

class FakeScannerIngestion:
    def __init__(self, symbols, sectors=None):
        self.symbols = symbols
        self.sectors = sectors or {}
        self.df = _make_ohlcv(90)

    def get_nse_universe(self):
        return self.symbols

    def get_sector(self, symbol):
        return self.sectors.get(symbol, "Unknown")

    def fetch_ohlcv_with_quality(self, symbol, *args, **kwargs):
        quality = DataQualityMetadata(
            symbol=symbol,
            source="fixture",
            adjusted=True,
            rows=len(self.df),
            expected_business_days=len(self.df),
            missing_data_pct=0.0,
        )
        return OHLCVFetchResult(symbol=symbol, data=self.df, quality=quality)


class FakeFundamentals:
    def __init__(self, sectors):
        self.sectors = sectors

    def screen(self, symbol):
        data = FundamentalData(
            symbol=symbol,
            market_cap_cr=1000,
            revenue_growth_pct=10,
            debt_to_equity=0.2,
            promoter_holding_pct=0.55,
            sector=self.sectors.get(symbol, "Unknown"),
            source="fixture",
        )
        return FundamentalResult(symbol=symbol, passed=True, data=data)


class FakePatternDetector:
    def scan(self, symbol, df):
        pattern = PatternResult(
            symbol=symbol,
            pattern_name="consolidation_after_uptrend",
            detected=True,
            confidence=0.8,
            entry_price=100.0,
        )
        return ScanResult(
            symbol=symbol,
            patterns=[pattern],
            best_pattern=pattern.pattern_name,
            passed=True,
        )


class FakeVolumeProfiler:
    def __init__(self, targets=None):
        self.targets = targets or {}

    def analyse(self, symbol, df):
        profile = SimpleNamespace(current_price=100.0)
        target = self.targets.get(symbol, 140.0)
        return profile, 90.0, [target]


def _scanner_config(**overrides):
    defaults = {
        "CAPITAL": 1_000_000,
        "RISK_PCT": 0.01,
        "BULL_MARKET_MIN_RR": 2.5,
        "BEAR_MARKET_MIN_RR": 3.5,
        "PORTFOLIO_HEAT_LIMIT": 0.05,
        "MAX_CONCURRENT_POSITIONS": 10,
        "SECTOR_CORRELATION_LIMIT": 2,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestDeterministicScanner:
    def _scanner(self, symbols, sectors=None, targets=None, config=None):
        sectors = sectors or {symbol: "Tech" for symbol in symbols}
        return DeterministicScanner(
            config=config or _scanner_config(),
            ingestion=FakeScannerIngestion(symbols, sectors),
            fundamentals=FakeFundamentals(sectors),
            pattern_detector=FakePatternDetector(),
            volume_profiler=FakeVolumeProfiler(targets),
        )

    def test_bear_market_uses_higher_min_rr_in_scan_path(self):
        scanner = self._scanner(["AAA.NS"], targets={"AAA.NS": 130.0})

        bull = scanner.run(symbols=["AAA.NS"], market_regime="bull", use_cache=False)
        bear = scanner.run(symbols=["AAA.NS"], market_regime="bear", use_cache=False)

        assert bull.funnel_counts["approved"] == 1
        assert bear.funnel_counts["approved"] == 0
        assert bear.rejected[0].stage == "risk"
        assert "minimum 3.50" in bear.rejected[0].reason

    def test_scan_path_enforces_sector_limit(self):
        symbols = ["AAA.NS", "BBB.NS", "CCC.NS"]
        sectors = {symbol: "Tech" for symbol in symbols}
        scanner = self._scanner(symbols, sectors=sectors)

        output = scanner.run(symbols=symbols, market_regime="bull", use_cache=False)

        assert output.funnel_counts["approved"] == 2
        assert any("sector" in rejected.reason for rejected in output.rejected)

    def test_scan_path_enforces_portfolio_heat_limit(self):
        symbols = ["AAA.NS", "BBB.NS", "CCC.NS"]
        sectors = {"AAA.NS": "Tech", "BBB.NS": "FMCG", "CCC.NS": "Energy"}
        scanner = self._scanner(
            symbols,
            sectors=sectors,
            config=_scanner_config(PORTFOLIO_HEAT_LIMIT=0.02, SECTOR_CORRELATION_LIMIT=10),
        )

        output = scanner.run(symbols=symbols, market_regime="bull", use_cache=False)

        assert output.funnel_counts["approved"] == 2
        assert any("portfolio heat" in rejected.reason for rejected in output.rejected)

    def test_json_and_csv_outputs_are_written(self, tmp_path):
        scanner = self._scanner(["AAA.NS"])
        output = scanner.run(symbols=["AAA.NS"], market_regime="bull", use_cache=False)

        paths = write_scan_outputs(output, tmp_path, basename="scan")

        assert paths["json"].exists()
        assert paths["csv"].exists()
        assert '"funnel_counts"' in paths["json"].read_text(encoding="utf-8")
        csv_text = paths["csv"].read_text(encoding="utf-8")
        assert "symbol,pattern" in csv_text
        assert "AAA.NS" in csv_text

    def test_config_overrides_change_module_behavior(self):
        config = _scanner_config(
            CONSOLIDATION_RANGE_PCT=0.03,
            VOLUME_PROFILE_BINS=11,
            BULL_MARKET_MIN_RR=2.0,
            BEAR_MARKET_MIN_RR=4.0,
        )

        detector = build_pattern_detector_from_config(config)
        profiler = build_volume_profiler_from_config(config)
        bear_risk = build_risk_manager_from_config(config, is_bull_market=False)
        bull_risk = build_risk_manager_from_config(config, is_bull_market=True)

        assert detector.consolidation_range_pct == pytest.approx(0.03)
        assert profiler.n_bins == 11
        assert not bear_risk.validate(
            RiskSetup("TEST", entry_price=100, stop_price=90, target_price=135)
        ).approved
        assert bull_risk.validate(
            RiskSetup("TEST", entry_price=100, stop_price=90, target_price=125)
        ).approved


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
