"""
Fundamental filter module.

Screens stocks against:
  - market cap
  - revenue growth
  - debt-to-equity
  - promoter holding

The primary deterministic path is CSV-backed so tests and dry scans can run
without network calls. yfinance remains as a fallback for ad-hoc live symbols.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import yfinance as yf

from modules.ingest import normalize_nse_symbol

logger = logging.getLogger(__name__)


@dataclass
class FundamentalData:
    """Fundamental snapshot for a single stock."""

    symbol: str
    market_cap_cr: Optional[float] = None
    revenue_growth_pct: Optional[float] = None
    debt_to_equity: Optional[float] = None
    promoter_holding_pct: Optional[float] = None  # stored as a fraction: 0.62 = 62%
    sector: str = "Unknown"
    source: str = "unknown"
    as_of: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FundamentalResult:
    """Output of the fundamental filter for one stock."""

    symbol: str
    passed: bool
    data: FundamentalData
    rejection_reason: Optional[str] = None


class FundamentalCSVSource:
    """Loads Screener.in-style fundamental snapshots from CSV files."""

    CANDIDATE_FILENAMES = (
        "fundamentals.csv",
        "fundamentals_fixture.csv",
        "screener_export.csv",
    )

    def __init__(
        self,
        fundamentals_dir: str = "./data/fundamentals",
        csv_path: Optional[str] = None,
    ):
        self.fundamentals_dir = Path(fundamentals_dir)
        self.csv_path = Path(csv_path) if csv_path else None
        self._records: Optional[Dict[str, FundamentalData]] = None

    def get(self, symbol: str) -> Optional[FundamentalData]:
        records = self._load_records()
        return records.get(normalize_nse_symbol(symbol))

    def _candidate_paths(self) -> Iterable[Path]:
        if self.csv_path:
            yield self.csv_path
            return
        for filename in self.CANDIDATE_FILENAMES:
            yield self.fundamentals_dir / filename
        if self.fundamentals_dir.exists():
            for path in sorted(self.fundamentals_dir.glob("*.csv")):
                if path.name not in self.CANDIDATE_FILENAMES:
                    yield path

    def _load_records(self) -> Dict[str, FundamentalData]:
        if self._records is not None:
            return self._records

        records: Dict[str, FundamentalData] = {}
        for path in self._candidate_paths():
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path)
            except Exception as exc:
                logger.warning("[fundamental] CSV read error for %s: %s", path, exc)
                continue
            records.update(self._records_from_dataframe(df, path))

        self._records = records
        return records

    def _records_from_dataframe(
        self,
        df: pd.DataFrame,
        path: Path,
    ) -> Dict[str, FundamentalData]:
        lower_to_actual = {c.lower().strip(): c for c in df.columns}
        symbol_col = self._pick_column(lower_to_actual, ("symbol", "ticker", "nse_symbol"))
        if not symbol_col:
            logger.warning("[fundamental] CSV %s missing symbol column", path)
            return {}

        output: Dict[str, FundamentalData] = {}
        for _, row in df.iterrows():
            symbol = normalize_nse_symbol(row.get(symbol_col, ""))
            if not symbol:
                continue
            raw = row.to_dict()
            output[symbol] = FundamentalData(
                symbol=symbol,
                market_cap_cr=self._number(row, lower_to_actual, ("market_cap_cr", "marketcap_cr", "market_cap")),
                revenue_growth_pct=self._growth_pct(row, lower_to_actual, ("revenue_growth_pct", "revenue_growth_yoy", "sales_growth_pct")),
                debt_to_equity=self._number(row, lower_to_actual, ("debt_to_equity", "debt_equity", "de")),
                promoter_holding_pct=self._fraction(row, lower_to_actual, ("promoter_holding_pct", "promoter_holding", "promoter_holding_percent")),
                sector=self._text(row, lower_to_actual, ("sector", "industry")) or "Unknown",
                source=f"csv:{path.name}",
                as_of=self._text(row, lower_to_actual, ("as_of", "date", "snapshot_date")),
                raw=raw,
            )
        return output

    def _pick_column(
        self,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> Optional[str]:
        for name in names:
            if name in lower_to_actual:
                return lower_to_actual[name]
        return None

    def _value(
        self,
        row: pd.Series,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> object:
        column = self._pick_column(lower_to_actual, names)
        if not column:
            return None
        value = row.get(column)
        if pd.isna(value):
            return None
        return value

    def _number(
        self,
        row: pd.Series,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> Optional[float]:
        value = self._value(row, lower_to_actual, names)
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "").replace("%", "").strip())
        except ValueError:
            return None

    def _growth_pct(
        self,
        row: pd.Series,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> Optional[float]:
        value = self._number(row, lower_to_actual, names)
        if value is None:
            return None
        return value * 100.0 if -1.0 < value < 1.0 else value

    def _fraction(
        self,
        row: pd.Series,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> Optional[float]:
        value = self._number(row, lower_to_actual, names)
        if value is None:
            return None
        return value / 100.0 if value > 1.0 else value

    def _text(
        self,
        row: pd.Series,
        lower_to_actual: Dict[str, str],
        names: Iterable[str],
    ) -> Optional[str]:
        value = self._value(row, lower_to_actual, names)
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class FundamentalFilter:
    """Applies fundamental screening criteria to NSE symbols."""

    def __init__(
        self,
        min_market_cap_cr: float = 500.0,
        min_revenue_growth: float = 0.0,
        max_debt_to_equity: float = 1.0,
        min_promoter_holding: float = 0.40,
        fundamentals_dir: str = "./data/fundamentals",
        csv_path: Optional[str] = None,
        prefer_csv: bool = True,
    ):
        self.min_market_cap_cr = min_market_cap_cr
        self.min_revenue_growth = min_revenue_growth
        self.max_debt_to_equity = max_debt_to_equity
        self.min_promoter_holding = min_promoter_holding
        self.csv_source = FundamentalCSVSource(fundamentals_dir, csv_path)
        self.prefer_csv = prefer_csv

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, symbol: str) -> FundamentalData:
        """Fetch a fundamental snapshot from CSV first, then yfinance."""

        normalized = normalize_nse_symbol(symbol)
        if self.prefer_csv:
            csv_data = self.csv_source.get(normalized)
            if csv_data is not None:
                return csv_data

        return self._fetch_yfinance(normalized)

    def _fetch_yfinance(self, symbol: str) -> FundamentalData:
        try:
            ticker = yf.Ticker(symbol)
            info: Dict[str, Any] = ticker.info or {}
        except Exception as exc:
            logger.warning("[fundamental] yfinance error for %s: %s", symbol, exc)
            info = {}

        market_cap_raw = info.get("marketCap")
        market_cap_cr = (market_cap_raw / 1e7) if market_cap_raw else None

        rev_growth_raw = info.get("revenueGrowth")
        revenue_growth_pct = (
            rev_growth_raw * 100.0 if rev_growth_raw is not None else None
        )

        dte_raw = info.get("debtToEquity")
        if dte_raw is not None and dte_raw > 20:
            dte_raw = dte_raw / 100.0

        return FundamentalData(
            symbol=symbol,
            market_cap_cr=market_cap_cr,
            revenue_growth_pct=revenue_growth_pct,
            debt_to_equity=dte_raw,
            promoter_holding_pct=None,
            sector=info.get("sector") or "Unknown",
            source="yfinance",
            raw=info,
        )

    def check(self, data: FundamentalData) -> FundamentalResult:
        """Apply all fundamental filters to a snapshot."""

        if data.market_cap_cr is None:
            return FundamentalResult(
                symbol=data.symbol,
                passed=False,
                data=data,
                rejection_reason="market_cap unavailable",
            )
        if data.market_cap_cr < self.min_market_cap_cr:
            return FundamentalResult(
                symbol=data.symbol,
                passed=False,
                data=data,
                rejection_reason=(
                    f"market_cap {data.market_cap_cr:.0f} Cr "
                    f"< {self.min_market_cap_cr:.0f} Cr"
                ),
            )

        if data.revenue_growth_pct is not None:
            if data.revenue_growth_pct < self.min_revenue_growth:
                return FundamentalResult(
                    symbol=data.symbol,
                    passed=False,
                    data=data,
                    rejection_reason=(
                        f"revenue_growth {data.revenue_growth_pct:.1f}% "
                        f"< {self.min_revenue_growth:.1f}%"
                    ),
                )

        if data.debt_to_equity is not None:
            if data.debt_to_equity > self.max_debt_to_equity:
                return FundamentalResult(
                    symbol=data.symbol,
                    passed=False,
                    data=data,
                    rejection_reason=(
                        f"debt_to_equity {data.debt_to_equity:.2f} "
                        f"> {self.max_debt_to_equity:.2f}"
                    ),
                )

        if data.promoter_holding_pct is not None:
            if data.promoter_holding_pct < self.min_promoter_holding:
                return FundamentalResult(
                    symbol=data.symbol,
                    passed=False,
                    data=data,
                    rejection_reason=(
                        f"promoter_holding {data.promoter_holding_pct:.1%} "
                        f"< {self.min_promoter_holding:.1%}"
                    ),
                )

        return FundamentalResult(symbol=data.symbol, passed=True, data=data)

    def screen(self, symbol: str) -> FundamentalResult:
        """Fetch and check one symbol."""

        data = self.fetch(symbol)
        result = self.check(data)
        logger.log(
            logging.INFO if result.passed else logging.DEBUG,
            "[fundamental] %s: %s",
            data.symbol,
            "PASS" if result.passed else f"FAIL - {result.rejection_reason or ''}",
        )
        return result

    def screen_universe(self, symbols: list) -> Dict[str, FundamentalResult]:
        """Screen a list of symbols; returns {symbol: FundamentalResult}."""

        results: Dict[str, FundamentalResult] = {}
        for symbol in symbols:
            result = self.screen(symbol)
            results[result.symbol] = result
        passed = sum(1 for r in results.values() if r.passed)
        logger.info("[fundamental] Universe screened: %s/%s passed", passed, len(symbols))
        return results
