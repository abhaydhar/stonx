"""
Fundamental Filter Module
Screens stocks against fundamental criteria:
  - Market cap >= MIN_MARKET_CAP_CR (₹ Crores)
  - Revenue growth >= MIN_REVENUE_GROWTH
  - Debt-to-equity <= MAX_DEBT_TO_EQUITY
  - Promoter holding >= MIN_PROMOTER_HOLDING

Data source: yfinance (market cap) + mock placeholders for
Screener.in fields (pending API integration).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class FundamentalData:
    """Fundamental snapshot for a single stock."""
    symbol: str
    market_cap_cr: Optional[float] = None       # ₹ Crores
    revenue_growth_pct: Optional[float] = None  # YoY %
    debt_to_equity: Optional[float] = None
    promoter_holding_pct: Optional[float] = None
    # raw info dict from yfinance for downstream use
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FundamentalResult:
    """Output of the fundamental filter for one stock."""
    symbol: str
    passed: bool
    data: FundamentalData
    rejection_reason: Optional[str] = None


class FundamentalFilter:
    """
    Applies fundamental screening criteria to a list of NSE symbols.

    Criteria (all configurable via ScannerConfig):
      1. Market cap  >= MIN_MARKET_CAP_CR  (default 500 Cr)
      2. Revenue growth >= MIN_REVENUE_GROWTH (default 0 %)
      3. Debt/equity <= MAX_DEBT_TO_EQUITY (default 1.0)
      4. Promoter holding >= MIN_PROMOTER_HOLDING (default 40 %)

    Note: Revenue growth and promoter holding are fetched from yfinance
    where available.  If the field is missing (common for Indian equities
    on yfinance) the check is skipped (benefit of the doubt).
    """

    def __init__(
        self,
        min_market_cap_cr: float = 500.0,
        min_revenue_growth: float = 0.0,
        max_debt_to_equity: float = 1.0,
        min_promoter_holding: float = 0.40,
    ):
        self.min_market_cap_cr = min_market_cap_cr
        self.min_revenue_growth = min_revenue_growth
        self.max_debt_to_equity = max_debt_to_equity
        self.min_promoter_holding = min_promoter_holding

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, symbol: str) -> FundamentalData:
        """
        Fetch fundamental data for a single symbol from yfinance.

        Returns FundamentalData with None for fields that could not be
        retrieved (caller decides how to treat missing values).
        """
        try:
            ticker = yf.Ticker(symbol)
            info: Dict[str, Any] = ticker.info or {}
        except Exception as exc:
            logger.warning(f"[fundamental] yfinance error for {symbol}: {exc}")
            info = {}

        # Market cap: yfinance returns in ₹ (for .NS symbols)
        market_cap_raw = info.get("marketCap")
        market_cap_cr = (market_cap_raw / 1e7) if market_cap_raw else None  # convert ₹ → Cr

        # Revenue growth: yfinance field 'revenueGrowth' (TTM YoY fraction)
        rev_growth_raw = info.get("revenueGrowth")  # e.g. 0.15 means +15 %
        revenue_growth_pct = (rev_growth_raw * 100.0) if rev_growth_raw is not None else None

        # Debt-to-equity: yfinance field 'debtToEquity' (already ratio)
        dte_raw = info.get("debtToEquity")
        # yfinance sometimes returns as percentage (e.g. 85 = 0.85), normalise
        if dte_raw is not None and dte_raw > 20:
            dte_raw = dte_raw / 100.0
        debt_to_equity = dte_raw

        # Promoter holding: not available via yfinance for NSE
        # Placeholder — will be integrated via Screener.in CSV later
        promoter_holding_pct = None

        return FundamentalData(
            symbol=symbol,
            market_cap_cr=market_cap_cr,
            revenue_growth_pct=revenue_growth_pct,
            debt_to_equity=debt_to_equity,
            promoter_holding_pct=promoter_holding_pct,
            raw=info,
        )

    def check(self, data: FundamentalData) -> FundamentalResult:
        """
        Apply all fundamental filters to a FundamentalData object.
        Missing (None) fields are treated as a pass (benefit of the doubt)
        except market cap which is always required.
        """
        # --- Market Cap (required) ---
        if data.market_cap_cr is None:
            return FundamentalResult(
                symbol=data.symbol, passed=False, data=data,
                rejection_reason="market_cap unavailable",
            )
        if data.market_cap_cr < self.min_market_cap_cr:
            return FundamentalResult(
                symbol=data.symbol, passed=False, data=data,
                rejection_reason=(
                    f"market_cap {data.market_cap_cr:.0f} Cr "
                    f"< {self.min_market_cap_cr:.0f} Cr"
                ),
            )

        # --- Revenue Growth (optional) ---
        if data.revenue_growth_pct is not None:
            if data.revenue_growth_pct < self.min_revenue_growth:
                return FundamentalResult(
                    symbol=data.symbol, passed=False, data=data,
                    rejection_reason=(
                        f"revenue_growth {data.revenue_growth_pct:.1f}% "
                        f"< {self.min_revenue_growth:.1f}%"
                    ),
                )

        # --- Debt-to-Equity (optional) ---
        if data.debt_to_equity is not None:
            if data.debt_to_equity > self.max_debt_to_equity:
                return FundamentalResult(
                    symbol=data.symbol, passed=False, data=data,
                    rejection_reason=(
                        f"debt_to_equity {data.debt_to_equity:.2f} "
                        f"> {self.max_debt_to_equity:.2f}"
                    ),
                )

        # --- Promoter Holding (optional — skip if not available) ---
        if data.promoter_holding_pct is not None:
            if data.promoter_holding_pct < self.min_promoter_holding:
                return FundamentalResult(
                    symbol=data.symbol, passed=False, data=data,
                    rejection_reason=(
                        f"promoter_holding {data.promoter_holding_pct:.1%} "
                        f"< {self.min_promoter_holding:.1%}"
                    ),
                )

        return FundamentalResult(symbol=data.symbol, passed=True, data=data)

    def screen(self, symbol: str) -> FundamentalResult:
        """Convenience: fetch + check in one call."""
        data = self.fetch(symbol)
        result = self.check(data)
        log_level = logging.INFO if result.passed else logging.DEBUG
        logger.log(
            log_level,
            f"[fundamental] {symbol}: "
            f"{'PASS' if result.passed else 'FAIL — ' + (result.rejection_reason or '')}",
        )
        return result

    def screen_universe(self, symbols: list) -> Dict[str, FundamentalResult]:
        """Screen a list of symbols; returns {symbol: FundamentalResult}."""
        results: Dict[str, FundamentalResult] = {}
        for sym in symbols:
            results[sym] = self.screen(sym)
        passed = sum(1 for r in results.values() if r.passed)
        logger.info(
            f"[fundamental] Universe screened: {passed}/{len(symbols)} passed"
        )
        return results
