"""
Analysis Tools — LangChain/CrewAI @tool wrappers around PatternDetector,
VolumeProfiler, RiskManager, and market-regime detection.

Each tool accepts a plain string and returns a JSON string.
"""

import json
import logging
from typing import Optional

from langchain.tools import tool
import pandas as pd
import yfinance as yf

from modules.ingest import DataIngestion
from modules.patterns import PatternDetector
from modules.volume import VolumeProfiler
from modules.risk import RiskManager, RiskSetup

logger = logging.getLogger(__name__)

# Singletons
_ingestion = DataIngestion(cache_dir="./data/cache")
_pattern_detector = PatternDetector()
_volume_profiler = VolumeProfiler()
_risk_manager = RiskManager()


# ---------------------------------------------------------------------------
# detect_patterns_tool
# ---------------------------------------------------------------------------

@tool
def detect_patterns_tool(symbol: str) -> str:
    """
    Detect technical breakout patterns for a single NSE/BSE stock symbol.

    Three patterns checked:
      1. consolidation_after_uptrend  — tight range after strong rally + breakout
      2. higher_lows                  — sequence of higher swing lows
      3. range_tightening             — ATR compression before expansion

    Input: stock symbol string like 'RELIANCE.NS'
    Returns: JSON with detected patterns, confidence scores, and key metrics.
    """
    symbol = symbol.strip().strip("'\"")
    df = _ingestion.fetch_ohlcv(symbol)

    if df is None or df.empty:
        return json.dumps({"symbol": symbol, "error": "No OHLCV data available"})

    try:
        scan = _pattern_detector.scan(symbol, df)
    except Exception as exc:
        logger.error(f"[detect_patterns_tool] {symbol}: {exc}")
        return json.dumps({"symbol": symbol, "error": str(exc)})

    patterns_out = []
    for p in scan.patterns:
        patterns_out.append({
            "name": p.pattern_name,
            "detected": p.detected,
            "confidence": p.confidence,
            "entry_price": p.entry_price,
            "consolidation_high": p.consolidation_high,
            "consolidation_low": p.consolidation_low,
            "consolidation_range_pct": p.consolidation_range_pct,
            "uptrend_gain_pct": p.uptrend_gain_pct,
            "volume_spike_ratio": p.volume_spike_ratio,
            "atr_ratio": p.atr_ratio,
            "swing_lows": p.swing_lows[-5:] if p.swing_lows else [],
            "notes": p.notes,
        })

    return json.dumps({
        "symbol": symbol,
        "passed": scan.passed,
        "best_pattern": scan.best_pattern,
        "patterns": patterns_out,
    })


# ---------------------------------------------------------------------------
# calculate_volume_profile_tool
# ---------------------------------------------------------------------------

@tool
def calculate_volume_profile_tool(symbol: str) -> str:
    """
    Calculate the volume profile (HVN / LVN) for a single NSE/BSE stock.

    High Volume Nodes (HVN) → strong support/resistance → use as stop-loss anchor.
    Low Volume Nodes  (LVN) → thin areas → price moves quickly → use as targets.

    Input: stock symbol string like 'TCS.NS'
    Returns: JSON with current price, HVN support level, and LVN target zones.
    """
    symbol = symbol.strip().strip("'\"")
    df = _ingestion.fetch_ohlcv(symbol)

    if df is None or df.empty:
        return json.dumps({"symbol": symbol, "error": "No OHLCV data available"})

    try:
        profile, hvn_support, lvn_targets = _volume_profiler.analyse(symbol, df)
    except Exception as exc:
        logger.error(f"[calculate_volume_profile_tool] {symbol}: {exc}")
        return json.dumps({"symbol": symbol, "error": str(exc)})

    if profile is None:
        return json.dumps({"symbol": symbol, "error": "Volume profile build failed"})

    return json.dumps({
        "symbol": symbol,
        "current_price": profile.current_price,
        "hvn_support": hvn_support,
        "hvn_levels_all": profile.hvn_levels,
        "lvn_targets": lvn_targets[:5],        # top 5 nearest targets
        "lvn_levels_all": profile.lvn_levels,
        "n_bins": len(profile.bin_midpoints),
    })


# ---------------------------------------------------------------------------
# validate_risk_reward_tool
# ---------------------------------------------------------------------------

@tool
def validate_risk_reward_tool(setup_json: str) -> str:
    """
    Validate the risk/reward ratio for a trade setup and calculate position size.

    Input JSON must contain:
      {
        "symbol":       "RELIANCE.NS",
        "entry_price":  2500.0,
        "stop_price":   2400.0,       (below entry — HVN support)
        "target_price": 2800.0,       (above entry — LVN target)
        "sector":       "Energy"      (optional, default "Unknown")
      }

    Returns JSON with approved (bool), R:R ratio, and position size details.
    """
    try:
        data = json.loads(setup_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    symbol = data.get("symbol", "UNKNOWN")
    try:
        setup = RiskSetup(
            symbol=symbol,
            entry_price=float(data["entry_price"]),
            stop_price=float(data["stop_price"]),
            target_price=float(data["target_price"]),
            sector=data.get("sector", "Unknown"),
        )
    except (KeyError, ValueError) as exc:
        return json.dumps({"error": f"Missing/invalid field: {exc}"})

    try:
        result = _risk_manager.validate(setup)
    except Exception as exc:
        logger.error(f"[validate_risk_reward_tool] {symbol}: {exc}")
        return json.dumps({"symbol": symbol, "error": str(exc)})

    return json.dumps({
        "symbol": symbol,
        "approved": result.approved,
        "rr_ratio": result.rr_ratio,
        "risk_per_share": result.risk_per_share,
        "position_size_shares": result.position_size_shares,
        "position_size_inr": result.position_size_inr,
        "capital_at_risk_inr": result.capital_at_risk_inr,
        "capital_at_risk_pct": result.capital_at_risk_pct,
        "rejection_reason": result.rejection_reason,
        "notes": result.notes,
    })


# ---------------------------------------------------------------------------
# check_market_regime_tool
# ---------------------------------------------------------------------------

@tool
def check_market_regime_tool(dummy_input: str = "") -> str:
    """
    Check the current market regime (bull or bear) based on Nifty 50 vs 200-day SMA.

    Bull market: Nifty 50 close > 200-day SMA
    Bear market: Nifty 50 close < 200-day SMA

    No input required.
    Returns JSON with regime, current Nifty price, and 200-day SMA.
    """
    try:
        df = _ingestion.fetch_ohlcv("^NSEI", use_cache=True)
        if df is None or df.empty:
            # Fallback: try yfinance directly with longer lookback
            import yfinance as yf
            from datetime import datetime, timedelta
            ticker = yf.Ticker("^NSEI")
            df = ticker.history(
                start=datetime.now() - timedelta(days=300),
                end=datetime.now(),
            )
    except Exception as exc:
        logger.warning(f"[check_market_regime_tool] Nifty fetch error: {exc}")
        return json.dumps({"regime": "unknown", "error": str(exc)})

    if df is None or len(df) < 200:
        return json.dumps({
            "regime": "unknown",
            "reason": f"Insufficient Nifty data ({len(df) if df is not None else 0} bars)",
        })

    current_price = float(df["Close"].iloc[-1])
    sma200 = float(df["Close"].rolling(200).mean().iloc[-1])
    regime = "bull" if current_price > sma200 else "bear"

    return json.dumps({
        "regime": regime,
        "nifty_close": round(current_price, 2),
        "sma_200": round(sma200, 2),
        "pct_above_sma": round((current_price - sma200) / sma200 * 100, 2),
        "min_rr_required": 2.5 if regime == "bull" else 3.5,
    })
