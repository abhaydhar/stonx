"""
Risk / Reward Gate & Position Sizing Module

Responsibilities:
  1. Calculate R:R ratio for a setup (entry, stop, target)
  2. Validate R:R against regime-adjusted minimum (bull 2.5x, bear 3.5x)
  3. Calculate position size using fixed fractional risk (1 % of capital)
  4. Enforce portfolio-level heat limit (total open risk ≤ 5 %)
  5. Enforce sector diversification limit

Design: pure functions / dataclasses — no side effects, fully testable.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RiskSetup:
    """Input for the risk calculator."""
    symbol: str
    entry_price: float
    stop_price: float        # stop loss level (from HVN support)
    target_price: float      # first price target (from LVN)
    sector: str = "Unknown"


@dataclass
class RiskResult:
    """Output of the risk gate for one setup."""
    symbol: str
    approved: bool
    rr_ratio: float
    risk_per_share: float
    position_size_shares: int
    position_size_inr: float
    capital_at_risk_inr: float
    capital_at_risk_pct: float
    rejection_reason: Optional[str] = None
    notes: str = ""


@dataclass
class PortfolioState:
    """Tracks current open positions for portfolio-level checks."""
    open_positions: Dict[str, RiskResult] = field(default_factory=dict)  # symbol → RiskResult
    sector_counts: Dict[str, int] = field(default_factory=dict)          # sector → count

    @property
    def total_heat(self) -> float:
        """Sum of capital_at_risk_pct across all open positions."""
        return sum(r.capital_at_risk_pct for r in self.open_positions.values())

    @property
    def position_count(self) -> int:
        return len(self.open_positions)


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Validates trade setups and calculates position sizes.

    Configuration mirrors ScannerConfig defaults:
      capital             = 10,00,000 ₹
      risk_pct            = 1 % per trade
      portfolio_heat_limit= 5 %
      min_rr_bull         = 2.5x
      min_rr_bear         = 3.5x
      max_concurrent      = 10 positions
      sector_limit        = 2 stocks per sector
    """

    def __init__(
        self,
        capital: float = 1_000_000.0,
        risk_pct: float = 0.01,
        min_rr_bull: float = 2.5,
        min_rr_bear: float = 3.5,
        portfolio_heat_limit: float = 0.05,
        max_concurrent_positions: int = 10,
        sector_correlation_limit: int = 2,
        is_bull_market: bool = True,
    ):
        self.capital = capital
        self.risk_pct = risk_pct
        self.min_rr_bull = min_rr_bull
        self.min_rr_bear = min_rr_bear
        self.portfolio_heat_limit = portfolio_heat_limit
        self.max_concurrent_positions = max_concurrent_positions
        self.sector_correlation_limit = sector_correlation_limit
        self.is_bull_market = is_bull_market

    @property
    def min_rr(self) -> float:
        return self.min_rr_bull if self.is_bull_market else self.min_rr_bear

    # ------------------------------------------------------------------
    # Core calculations (stateless)
    # ------------------------------------------------------------------

    def calculate_rr(self, setup: RiskSetup) -> float:
        """
        R:R = (target - entry) / (entry - stop)
        Both numerator and denominator must be positive; returns -1 on invalid.
        """
        reward = setup.target_price - setup.entry_price
        risk = setup.entry_price - setup.stop_price

        if risk <= 0:
            logger.warning(
                f"[risk] {setup.symbol}: stop ({setup.stop_price:.2f}) >= "
                f"entry ({setup.entry_price:.2f})"
            )
            return -1.0

        if reward <= 0:
            logger.warning(
                f"[risk] {setup.symbol}: target ({setup.target_price:.2f}) <= "
                f"entry ({setup.entry_price:.2f})"
            )
            return -1.0

        return reward / risk

    def calculate_position_size(self, setup: RiskSetup) -> tuple:
        """
        Fixed fractional sizing: risk 1 % of capital per trade.

        capital_at_risk = capital × risk_pct
        shares = capital_at_risk / risk_per_share
        position_inr = shares × entry_price

        Returns: (shares: int, position_inr: float, capital_at_risk_inr: float)
        """
        capital_at_risk_inr = self.capital * self.risk_pct
        risk_per_share = setup.entry_price - setup.stop_price

        if risk_per_share <= 0:
            return 0, 0.0, 0.0

        shares = int(capital_at_risk_inr / risk_per_share)
        position_inr = shares * setup.entry_price

        return shares, round(position_inr, 2), round(capital_at_risk_inr, 2)

    # ------------------------------------------------------------------
    # Validation gate
    # ------------------------------------------------------------------

    def validate(
        self,
        setup: RiskSetup,
        portfolio: Optional[PortfolioState] = None,
    ) -> RiskResult:
        """
        Validate a trade setup and return RiskResult with approved=True/False.

        Checks (in order):
          1. Stop below entry
          2. Target above entry
          3. R:R >= min_rr
          4. Portfolio heat limit (if portfolio provided)
          5. Max concurrent positions (if portfolio provided)
          6. Sector diversification (if portfolio provided)
        """
        rr = self.calculate_rr(setup)
        shares, position_inr, capital_at_risk_inr = self.calculate_position_size(setup)
        capital_at_risk_pct = capital_at_risk_inr / self.capital if self.capital > 0 else 0.0

        base = RiskResult(
            symbol=setup.symbol,
            approved=False,
            rr_ratio=round(rr, 2),
            risk_per_share=round(setup.entry_price - setup.stop_price, 2),
            position_size_shares=shares,
            position_size_inr=position_inr,
            capital_at_risk_inr=capital_at_risk_inr,
            capital_at_risk_pct=round(capital_at_risk_pct, 4),
        )

        # --- 1. Valid stop ---
        if setup.stop_price >= setup.entry_price:
            base.rejection_reason = "stop >= entry"
            logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
            return base

        # --- 2. Valid target ---
        if setup.target_price <= setup.entry_price:
            base.rejection_reason = "target <= entry"
            logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
            return base

        # --- 3. R:R gate ---
        if rr < self.min_rr:
            base.rejection_reason = (
                f"R:R {rr:.2f} < minimum {self.min_rr:.2f} "
                f"({'bull' if self.is_bull_market else 'bear'} market)"
            )
            logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
            return base

        # --- Portfolio-level checks ---
        if portfolio is not None:

            # 4. Heat limit
            projected_heat = portfolio.total_heat + capital_at_risk_pct
            if projected_heat > self.portfolio_heat_limit:
                base.rejection_reason = (
                    f"portfolio heat {projected_heat:.1%} > limit "
                    f"{self.portfolio_heat_limit:.1%}"
                )
                logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
                return base

            # 5. Max concurrent positions
            if portfolio.position_count >= self.max_concurrent_positions:
                base.rejection_reason = (
                    f"max positions ({self.max_concurrent_positions}) already open"
                )
                logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
                return base

            # 6. Sector concentration
            sector_count = portfolio.sector_counts.get(setup.sector, 0)
            if sector_count >= self.sector_correlation_limit:
                base.rejection_reason = (
                    f"sector '{setup.sector}' already has "
                    f"{sector_count} positions (limit {self.sector_correlation_limit})"
                )
                logger.debug(f"[risk] {setup.symbol}: FAIL — {base.rejection_reason}")
                return base

        base.approved = True
        base.notes = (
            f"R:R {rr:.2f}x | "
            f"size {shares} shares × ₹{setup.entry_price:.2f} = "
            f"₹{position_inr:,.0f} | risk ₹{capital_at_risk_inr:,.0f}"
        )
        logger.info(f"[risk] {setup.symbol}: APPROVED — {base.notes}")
        return base

    # ------------------------------------------------------------------
    # Batch validation
    # ------------------------------------------------------------------

    def validate_batch(
        self,
        setups: List[RiskSetup],
        portfolio: Optional[PortfolioState] = None,
    ) -> List[RiskResult]:
        """
        Validate a list of setups in priority order (best R:R first).
        Returns all results (approved and rejected).
        """
        # Sort by estimated R:R descending so best setups claim heat first
        sorted_setups = sorted(
            setups,
            key=lambda s: self.calculate_rr(s),
            reverse=True,
        )

        results: List[RiskResult] = []
        live_portfolio = portfolio or PortfolioState()

        for setup in sorted_setups:
            result = self.validate(setup, live_portfolio)
            results.append(result)

            if result.approved:
                # Update running portfolio state for subsequent checks
                live_portfolio.open_positions[setup.symbol] = result
                sector = setup.sector
                live_portfolio.sector_counts[sector] = (
                    live_portfolio.sector_counts.get(sector, 0) + 1
                )

        approved = sum(1 for r in results if r.approved)
        logger.info(
            f"[risk] Batch validation: {approved}/{len(setups)} approved"
        )
        return results
