"""
Deterministic scanner service.

This module is the application-logic spine for the scanner. It runs the
fundamental -> data -> pattern -> volume -> risk funnel without LLM calls,
enforces market regime and portfolio constraints in the scan path, and writes
JSON/CSV outputs.
"""

from __future__ import annotations

import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from modules.fundamental import FundamentalFilter
from modules.ingest import DataIngestion, normalize_nse_symbol
from modules.patterns import PatternDetector, PatternResult
from modules.risk import PortfolioState, RiskManager, RiskResult, RiskSetup
from modules.volume import VolumeProfiler

logger = logging.getLogger(__name__)


def _cfg(config: object, name: str, default: Any) -> Any:
    return getattr(config, name, default)


def load_scanner_config() -> object:
    """
    Load ScannerConfig when available.

    The deterministic scanner does not need API keys. If the project config
    rejects construction because LLM credentials are absent, provide the
    required key locally instead of editing config.py.
    """

    try:
        from config import ScannerConfig, get_config
    except Exception as exc:
        logger.debug("[scanner] config import unavailable: %s", exc)
        return SimpleNamespace()

    try:
        return get_config()
    except Exception:
        try:
            return ScannerConfig(ANTHROPIC_API_KEY="not-required-for-deterministic-scan")
        except Exception as exc:
            logger.debug("[scanner] config fallback unavailable: %s", exc)
            return SimpleNamespace()


def build_ingestion_from_config(
    config: object,
    provider: Optional[object] = None,
) -> DataIngestion:
    return DataIngestion(
        cache_dir=_cfg(config, "DATA_CACHE_DIR", "./data/cache"),
        provider=provider,
    )


def build_fundamental_filter_from_config(config: object) -> FundamentalFilter:
    return FundamentalFilter(
        min_market_cap_cr=_cfg(config, "MIN_MARKET_CAP_CR", 500.0),
        min_revenue_growth=_cfg(config, "MIN_REVENUE_GROWTH", 0.0),
        max_debt_to_equity=_cfg(config, "MAX_DEBT_TO_EQUITY", 1.0),
        min_promoter_holding=_cfg(config, "MIN_PROMOTER_HOLDING", 0.40),
        fundamentals_dir=_cfg(config, "FUNDAMENTALS_DIR", "./data/fundamentals"),
    )


def build_pattern_detector_from_config(config: object) -> PatternDetector:
    return PatternDetector(
        consolidation_days=_cfg(config, "CONSOLIDATION_DAYS", 20),
        consolidation_range_pct=_cfg(config, "CONSOLIDATION_RANGE_PCT", 0.08),
        uptrend_lookback_days=_cfg(config, "UPTREND_LOOKBACK_DAYS", 60),
        uptrend_min_gain_pct=_cfg(config, "UPTREND_MIN_GAIN_PCT", 0.20),
        volume_spike_multiplier=_cfg(config, "VOLUME_SPIKE_MULTIPLIER", 1.5),
        atr_compression_ratio=_cfg(config, "ATR_COMPRESSION_RATIO", 0.70),
        breakout_hold_bars=_cfg(config, "BREAKOUT_HOLD_BARS", 2),
    )


def build_volume_profiler_from_config(config: object) -> VolumeProfiler:
    return VolumeProfiler(
        n_bins=_cfg(config, "VOLUME_PROFILE_BINS", 20),
        hvn_threshold=_cfg(config, "HVN_THRESHOLD", 1.5),
        lvn_threshold=_cfg(config, "LVN_THRESHOLD", 0.5),
    )


def build_risk_manager_from_config(
    config: object,
    is_bull_market: bool = True,
) -> RiskManager:
    return RiskManager(
        capital=_cfg(config, "CAPITAL", 1_000_000.0),
        risk_pct=_cfg(config, "RISK_PCT", 0.01),
        min_rr_bull=_cfg(config, "BULL_MARKET_MIN_RR", _cfg(config, "MIN_RR", 2.5)),
        min_rr_bear=_cfg(config, "BEAR_MARKET_MIN_RR", 3.5),
        portfolio_heat_limit=_cfg(config, "PORTFOLIO_HEAT_LIMIT", 0.05),
        max_concurrent_positions=_cfg(config, "MAX_CONCURRENT_POSITIONS", 10),
        sector_correlation_limit=_cfg(config, "SECTOR_CORRELATION_LIMIT", 2),
        is_bull_market=is_bull_market,
    )


@dataclass(frozen=True)
class MarketRegime:
    regime: str
    is_bull_market: bool
    min_rr_required: float
    source: str = "override"
    nifty_close: Optional[float] = None
    sma: Optional[float] = None
    pct_above_sma: Optional[float] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScannerCandidate:
    rank: int
    symbol: str
    pattern: str
    confidence: float
    entry: float
    stop: float
    target: float
    rr_ratio: float
    position_shares: int
    position_inr: float
    capital_at_risk_inr: float
    capital_at_risk_pct: float
    sector: str
    market_regime: str
    risk_status: str = "approved"
    rationale: str = ""


@dataclass
class RejectedSetup:
    symbol: str
    stage: str
    reason: str
    sector: str = "Unknown"
    pattern: Optional[str] = None
    rr_ratio: Optional[float] = None


@dataclass
class ScannerOutput:
    timestamp: str
    market_regime: MarketRegime
    funnel_counts: Dict[str, int]
    candidates: List[ScannerCandidate] = field(default_factory=list)
    rejected: List[RejectedSetup] = field(default_factory=list)
    data_quality: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "market_regime": self.market_regime.to_dict(),
            "funnel_counts": self.funnel_counts,
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "rejected": [asdict(rejected) for rejected in self.rejected],
            "data_quality": self.data_quality,
        }


class DeterministicScanner:
    """Runs the deterministic scanner funnel."""

    def __init__(
        self,
        config: Optional[object] = None,
        ingestion: Optional[DataIngestion] = None,
        fundamentals: Optional[FundamentalFilter] = None,
        pattern_detector: Optional[PatternDetector] = None,
        volume_profiler: Optional[VolumeProfiler] = None,
    ):
        self.config = config or load_scanner_config()
        self.ingestion = ingestion or build_ingestion_from_config(self.config)
        self.fundamentals = fundamentals or build_fundamental_filter_from_config(self.config)
        self.pattern_detector = pattern_detector or build_pattern_detector_from_config(self.config)
        self.volume_profiler = volume_profiler or build_volume_profiler_from_config(self.config)

    def run(
        self,
        symbols: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
        market_regime: Optional[str] = None,
        portfolio: Optional[PortfolioState] = None,
        use_cache: bool = True,
        max_workers: int = 8,
    ) -> ScannerOutput:
        """Run the full deterministic funnel and return structured output.

        The fundamental/data/pattern/volume stages are independent per symbol
        (each is a separate network fetch plus pure computation), so they run
        concurrently across up to ``max_workers`` threads. The risk gate is
        stateful (portfolio heat, max positions) and always runs sequentially
        afterward, in ranked order, so results are deterministic regardless
        of how the parallel stage completes.
        """

        normalized_symbols = self._resolve_symbols(symbols, limit)
        regime = self.detect_market_regime(market_regime, use_cache=use_cache)
        risk_manager = build_risk_manager_from_config(
            self.config,
            is_bull_market=regime.is_bull_market,
        )

        counts = {
            "universe_total": len(normalized_symbols),
            "passed_fundamental": 0,
            "data_loaded": 0,
            "passed_pattern": 0,
            "passed_volume": 0,
            "risk_evaluated": 0,
            "approved": 0,
            "rejected": 0,
        }
        rejected: List[RejectedSetup] = []
        data_quality: Dict[str, Dict[str, Any]] = {}
        contexts: Dict[str, Dict[str, Any]] = {}
        risk_setups: List[RiskSetup] = []

        if normalized_symbols:
            workers = max(1, min(max_workers, len(normalized_symbols)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(
                    executor.map(
                        lambda symbol: self._evaluate_symbol(symbol, regime, use_cache),
                        normalized_symbols,
                    )
                )
        else:
            results = []

        # Stages a symbol clears before hitting the stage it was rejected at
        # (or all of them, if it produced a risk setup).
        stage_order = ["fundamental", "data", "pattern", "volume"]
        count_keys = ["passed_fundamental", "data_loaded", "passed_pattern", "passed_volume"]

        for symbol_rejected, quality, setup, context in results:
            if quality is not None:
                data_quality[quality[0]] = quality[1]
            if symbol_rejected is not None:
                rejected.append(symbol_rejected)
                cleared = stage_order.index(symbol_rejected.stage)
                for key in count_keys[:cleared]:
                    counts[key] += 1
                continue
            for key in count_keys:
                counts[key] += 1
            risk_setups.append(setup)
            contexts[setup.symbol] = context

        risk_results = risk_manager.validate_batch(risk_setups, portfolio)
        counts["risk_evaluated"] = len(risk_results)

        candidates: List[ScannerCandidate] = []
        for risk_result in risk_results:
            context = contexts[risk_result.symbol]
            if risk_result.approved:
                candidates.append(
                    self._candidate_from_result(
                        risk_result=risk_result,
                        context=context,
                        market_regime=regime.regime,
                    )
                )
            else:
                pattern = context["pattern"]
                rejected.append(
                    RejectedSetup(
                        symbol=risk_result.symbol,
                        stage="risk",
                        reason=risk_result.rejection_reason or "risk gate failed",
                        sector=context["sector"],
                        pattern=pattern.pattern_name,
                        rr_ratio=risk_result.rr_ratio,
                    )
                )

        candidates.sort(key=lambda item: (item.rr_ratio, item.confidence), reverse=True)
        for rank, candidate in enumerate(candidates, start=1):
            candidate.rank = rank

        counts["approved"] = len(candidates)
        counts["rejected"] = len(rejected)

        return ScannerOutput(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            market_regime=regime,
            funnel_counts=counts,
            candidates=candidates,
            rejected=rejected,
            data_quality=data_quality,
        )

    def detect_market_regime(
        self,
        override: Optional[str] = None,
        use_cache: bool = True,
    ) -> MarketRegime:
        """Detect bull/bear regime from Nifty SMA, or use a caller override."""

        bull_rr = _cfg(self.config, "BULL_MARKET_MIN_RR", _cfg(self.config, "MIN_RR", 2.5))
        bear_rr = _cfg(self.config, "BEAR_MARKET_MIN_RR", 3.5)

        if override:
            regime = override.strip().lower()
            if regime not in {"bull", "bear"}:
                regime = "unknown"
            is_bull = regime != "bear"
            return MarketRegime(
                regime=regime,
                is_bull_market=is_bull,
                min_rr_required=bull_rr if is_bull else bear_rr,
                source="override",
            )

        sma_period = int(_cfg(self.config, "NIFTY_SMA_PERIOD", 200))
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(320, int(sma_period * 1.6)))
        fetched = self.ingestion.fetch_ohlcv_with_quality(
            "^NSEI",
            start_date=start_date,
            end_date=end_date,
            use_cache=use_cache,
        )
        df = fetched.data
        if df is None or len(df) < sma_period:
            return MarketRegime(
                regime="unknown",
                is_bull_market=True,
                min_rr_required=bull_rr,
                source=fetched.quality.source,
                reason=f"Insufficient Nifty data ({len(df) if df is not None else 0} bars)",
            )

        current_price = float(df["Close"].iloc[-1])
        sma = float(df["Close"].rolling(sma_period).mean().iloc[-1])
        is_bull = current_price > sma
        return MarketRegime(
            regime="bull" if is_bull else "bear",
            is_bull_market=is_bull,
            min_rr_required=bull_rr if is_bull else bear_rr,
            source=fetched.quality.source,
            nifty_close=round(current_price, 2),
            sma=round(sma, 2),
            pct_above_sma=round((current_price - sma) / sma * 100, 2) if sma else None,
        )

    def _evaluate_symbol(
        self,
        symbol: str,
        regime: MarketRegime,
        use_cache: bool,
    ) -> Tuple[
        Optional[RejectedSetup],
        Optional[Tuple[str, Dict[str, Any]]],
        Optional[RiskSetup],
        Optional[Dict[str, Any]],
    ]:
        """Run the fundamental/data/pattern/volume stages for one symbol.

        Safe to call from multiple threads: each symbol only touches its own
        cache file and returns its own result, so there's no shared state
        across concurrent calls. Returns (rejection, data_quality_entry,
        risk_setup, context) with exactly one of (rejection, risk_setup) set.
        """

        fundamental = self.fundamentals.screen(symbol)
        sector = self._sector(symbol, fundamental.data.sector)
        if not fundamental.passed:
            rejection = RejectedSetup(
                symbol=fundamental.symbol,
                stage="fundamental",
                reason=fundamental.rejection_reason or "fundamental filter failed",
                sector=sector,
            )
            return rejection, None, None, None

        fetched = self.ingestion.fetch_ohlcv_with_quality(symbol, use_cache=use_cache)
        quality_entry = (fetched.symbol, fetched.quality.to_dict())
        if fetched.data is None or fetched.data.empty:
            rejection = RejectedSetup(
                symbol=fetched.symbol,
                stage="data",
                reason=fetched.quality.error or "OHLCV unavailable",
                sector=sector,
            )
            return rejection, quality_entry, None, None

        pattern_scan = self.pattern_detector.scan(fetched.symbol, fetched.data)
        best_pattern = self._best_detected_pattern(pattern_scan.patterns)
        if not pattern_scan.passed or best_pattern is None:
            rejection = RejectedSetup(
                symbol=fetched.symbol,
                stage="pattern",
                reason="no technical pattern detected",
                sector=sector,
            )
            return rejection, quality_entry, None, None

        profile, hvn_support, lvn_targets = self.volume_profiler.analyse(
            fetched.symbol,
            fetched.data,
        )
        if profile is None or hvn_support is None or not lvn_targets:
            rejection = RejectedSetup(
                symbol=fetched.symbol,
                stage="volume",
                reason="missing HVN support or LVN target",
                sector=sector,
                pattern=best_pattern.pattern_name,
            )
            return rejection, quality_entry, None, None

        target = self._select_target(
            entry=profile.current_price,
            stop=hvn_support,
            targets=lvn_targets,
            min_rr=regime.min_rr_required,
        )
        setup = RiskSetup(
            symbol=fetched.symbol,
            entry_price=float(profile.current_price),
            stop_price=float(hvn_support),
            target_price=float(target),
            sector=sector,
        )
        context = {
            "fundamental": fundamental,
            "pattern": best_pattern,
            "profile": profile,
            "setup": setup,
            "sector": sector,
        }
        return None, quality_entry, setup, context

    def _resolve_symbols(
        self,
        symbols: Optional[Iterable[str]],
        limit: Optional[int],
    ) -> List[str]:
        if symbols is None:
            resolved = self.ingestion.get_nse_universe()
        else:
            resolved = [normalize_nse_symbol(symbol) for symbol in symbols if str(symbol).strip()]
        return resolved[:limit] if limit else resolved

    def _sector(self, symbol: str, fundamental_sector: str) -> str:
        if fundamental_sector and fundamental_sector != "Unknown":
            return fundamental_sector
        return self.ingestion.get_sector(symbol)

    def _best_detected_pattern(
        self,
        patterns: List[PatternResult],
    ) -> Optional[PatternResult]:
        detected = [pattern for pattern in patterns if pattern.detected]
        if not detected:
            return None
        return max(detected, key=lambda pattern: pattern.confidence)

    def _select_target(
        self,
        entry: float,
        stop: float,
        targets: List[float],
        min_rr: float,
    ) -> float:
        sorted_targets = sorted(target for target in targets if target > entry)
        if not sorted_targets:
            return targets[0]
        risk = entry - stop
        if risk <= 0:
            return sorted_targets[0]
        for target in sorted_targets:
            if (target - entry) / risk >= min_rr:
                return target
        return sorted_targets[0]

    def _candidate_from_result(
        self,
        risk_result: RiskResult,
        context: Dict[str, Any],
        market_regime: str,
    ) -> ScannerCandidate:
        pattern = context["pattern"]
        setup = context["setup"]
        return ScannerCandidate(
            rank=0,
            symbol=risk_result.symbol,
            pattern=pattern.pattern_name,
            confidence=pattern.confidence,
            entry=round(setup.entry_price, 2),
            stop=round(setup.stop_price, 2),
            target=round(setup.target_price, 2),
            rr_ratio=risk_result.rr_ratio,
            position_shares=risk_result.position_size_shares,
            position_inr=risk_result.position_size_inr,
            capital_at_risk_inr=risk_result.capital_at_risk_inr,
            capital_at_risk_pct=risk_result.capital_at_risk_pct,
            sector=context["sector"],
            market_regime=market_regime,
            rationale=(
                f"{pattern.pattern_name} with R:R {risk_result.rr_ratio:.2f}x "
                f"and {context['sector']} sector exposure within limits."
            ),
        )


def run_deterministic_scan(
    symbols: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    market_regime: Optional[str] = None,
    output_dir: Optional[str] = None,
    use_cache: bool = True,
) -> ScannerOutput:
    scanner = DeterministicScanner()
    output = scanner.run(
        symbols=symbols,
        limit=limit,
        market_regime=market_regime,
        use_cache=use_cache,
    )
    if output_dir:
        write_scan_outputs(output, output_dir)
    return output


def write_scan_outputs(
    output: ScannerOutput,
    output_dir: str | Path = "./data",
    basename: Optional[str] = None,
) -> Dict[str, Path]:
    """Write scanner output to JSON plus candidate CSV."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = basename or f"scan_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    json_path = directory / f"{stem}.json"
    csv_path = directory / f"{stem}.csv"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(output.to_dict(), handle, indent=2)

    fieldnames = [
        "rank",
        "symbol",
        "pattern",
        "confidence",
        "entry",
        "stop",
        "target",
        "rr_ratio",
        "position_shares",
        "position_inr",
        "capital_at_risk_inr",
        "capital_at_risk_pct",
        "sector",
        "market_regime",
        "risk_status",
        "rationale",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in output.candidates:
            writer.writerow(asdict(candidate))

    return {"json": json_path, "csv": csv_path}
