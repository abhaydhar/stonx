"""
Volume Profile Module
Calculates High Volume Nodes (HVN) and Low Volume Nodes (LVN) from
OHLCV data using price-bin histograms.

HVN = price levels where > HVN_THRESHOLD × mean volume traded
       → acts as support/resistance; used as stop-loss anchor
LVN = price levels where < LVN_THRESHOLD × mean volume traded
       → thin zone; price moves quickly through → used as targets

Flow:
  build_profile()  →  VolumeProfile
  find_hvn_support()  →  nearest HVN below current price  (stop loss)
  find_lvn_targets()  →  LVN zones above current price    (targets)
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
class VolumeProfile:
    """Price-volume histogram for one stock."""
    symbol: str
    bin_edges: List[float]         # N+1 bin boundaries
    bin_midpoints: List[float]     # N midpoints
    bin_volumes: List[float]       # total volume in each bin
    mean_volume: float
    hvn_levels: List[float]        # midpoints classified as HVN
    lvn_levels: List[float]        # midpoints classified as LVN
    current_price: float


# ---------------------------------------------------------------------------
# VolumeProfiler
# ---------------------------------------------------------------------------

class VolumeProfiler:
    """
    Builds a volume profile from OHLCV data and identifies HVN / LVN zones.

    Algorithm:
      1. Slice each bar into N equal-width price bins covering the
         bar's High–Low range, distributing the bar's volume evenly.
      2. Sum across all bars to get total volume per price bin.
      3. Classify bins: HVN if vol > mean × hvn_threshold;
                        LVN if vol < mean × lvn_threshold.
    """

    def __init__(
        self,
        n_bins: int = 20,
        hvn_threshold: float = 1.5,
        lvn_threshold: float = 0.5,
    ):
        self.n_bins = n_bins
        self.hvn_threshold = hvn_threshold
        self.lvn_threshold = lvn_threshold

    # ------------------------------------------------------------------
    # Profile construction
    # ------------------------------------------------------------------

    def build_profile(self, symbol: str, df: pd.DataFrame) -> Optional[VolumeProfile]:
        """
        Build VolumeProfile from a OHLCV DataFrame.

        Args:
            symbol:  ticker symbol (logging only)
            df:      DataFrame with columns High, Low, Close, Volume

        Returns:
            VolumeProfile or None if data insufficient
        """
        if df is None or len(df) < 20:
            logger.warning(f"[volume] {symbol}: insufficient data")
            return None

        price_min = df["Low"].min()
        price_max = df["High"].max()
        if price_max <= price_min:
            logger.warning(f"[volume] {symbol}: zero price range")
            return None

        edges = np.linspace(price_min, price_max, self.n_bins + 1)
        bin_volumes = np.zeros(self.n_bins, dtype=float)

        for _, row in df.iterrows():
            bar_low = row["Low"]
            bar_high = row["High"]
            bar_vol = row["Volume"]

            if bar_vol <= 0 or pd.isna(bar_vol):
                continue
            if bar_high <= bar_low:
                continue

            # find bins that overlap this bar's [low, high] range
            # weight volume proportionally to bin overlap
            for b in range(self.n_bins):
                bin_lo = edges[b]
                bin_hi = edges[b + 1]
                overlap = min(bar_high, bin_hi) - max(bar_low, bin_lo)
                if overlap > 0:
                    bar_range = bar_high - bar_low
                    bin_volumes[b] += bar_vol * (overlap / bar_range)

        midpoints = [(edges[i] + edges[i + 1]) / 2 for i in range(self.n_bins)]
        mean_vol = float(bin_volumes.mean()) if bin_volumes.sum() > 0 else 0.0

        hvn_levels: List[float] = []
        lvn_levels: List[float] = []

        for mid, vol in zip(midpoints, bin_volumes):
            if mean_vol > 0:
                if vol >= mean_vol * self.hvn_threshold:
                    hvn_levels.append(round(mid, 2))
                elif vol <= mean_vol * self.lvn_threshold:
                    lvn_levels.append(round(mid, 2))

        current_price = float(df["Close"].iloc[-1])

        profile = VolumeProfile(
            symbol=symbol,
            bin_edges=[round(e, 2) for e in edges.tolist()],
            bin_midpoints=[round(m, 2) for m in midpoints],
            bin_volumes=bin_volumes.tolist(),
            mean_volume=round(mean_vol, 2),
            hvn_levels=sorted(hvn_levels),
            lvn_levels=sorted(lvn_levels),
            current_price=current_price,
        )

        logger.info(
            f"[volume] {symbol}: {len(hvn_levels)} HVNs, "
            f"{len(lvn_levels)} LVNs | price {current_price:.2f}"
        )
        return profile

    # ------------------------------------------------------------------
    # Stop / Target helpers
    # ------------------------------------------------------------------

    def find_hvn_support(self, profile: VolumeProfile) -> Optional[float]:
        """
        Return the nearest HVN level *below* current price.
        This is the natural stop-loss anchor (strong support).
        """
        candidates = [h for h in profile.hvn_levels if h < profile.current_price]
        if not candidates:
            logger.debug(
                f"[volume] {profile.symbol}: no HVN below {profile.current_price:.2f}"
            )
            return None
        nearest = max(candidates)  # closest below
        logger.debug(
            f"[volume] {profile.symbol}: HVN support at {nearest:.2f}"
        )
        return nearest

    def find_lvn_targets(
        self, profile: VolumeProfile, min_rr: float = 1.0
    ) -> List[float]:
        """
        Return LVN levels *above* current price, sorted ascending.
        These are areas of low resistance → potential price targets.

        Args:
            profile:  VolumeProfile object
            min_rr:   only return LVNs above the first one for target selection;
                      the caller uses these to compute R:R
        """
        targets = sorted(
            [lv for lv in profile.lvn_levels if lv > profile.current_price]
        )
        logger.debug(
            f"[volume] {profile.symbol}: {len(targets)} LVN targets above price"
        )
        return targets

    # ------------------------------------------------------------------
    # Convenience: full analysis
    # ------------------------------------------------------------------

    def analyse(
        self, symbol: str, df: pd.DataFrame
    ) -> Tuple[Optional[VolumeProfile], Optional[float], List[float]]:
        """
        Build profile and immediately return (profile, hvn_support, lvn_targets).
        """
        profile = self.build_profile(symbol, df)
        if profile is None:
            return None, None, []
        hvn = self.find_hvn_support(profile)
        lvns = self.find_lvn_targets(profile)
        return profile, hvn, lvns
