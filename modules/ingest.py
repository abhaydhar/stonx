"""
Data ingestion module.

Provides:
  - pluggable OHLCV providers
  - CSV-backed NSE universe loading
  - cache-aware OHLCV fetches with data quality metadata
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Protocol

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniverseMember:
    """One stock in the scan universe."""

    symbol: str
    name: str = ""
    sector: str = "Unknown"


@dataclass(frozen=True)
class DataQualityMetadata:
    """Quality metadata for an OHLCV fetch."""

    symbol: str
    source: str
    adjusted: bool
    rows: int
    expected_business_days: int
    missing_data_pct: float
    cache_hit: bool = False
    cache_age_seconds: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    first_bar_date: Optional[str] = None
    last_bar_date: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OHLCVFetchResult:
    """OHLCV data plus fetch/quality metadata."""

    symbol: str
    data: Optional[pd.DataFrame]
    quality: DataQualityMetadata


@dataclass(frozen=True)
class _CachedOHLCV:
    data: pd.DataFrame
    age: timedelta


class OHLCVProvider(Protocol):
    """Provider interface for OHLCV data sources."""

    source_name: str
    adjusted: bool

    def fetch(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        ...


class YFinanceOHLCVProvider:
    """OHLCV provider backed by yfinance."""

    source_name = "yfinance"

    def __init__(self, auto_adjust: bool = False):
        self.adjusted = auto_adjust
        self.auto_adjust = auto_adjust

    def fetch(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        return ticker.history(
            start=start_date,
            end=end_date,
            auto_adjust=self.auto_adjust,
        )


def normalize_nse_symbol(symbol: str) -> str:
    """Normalize a user/CSV symbol to yfinance's NSE suffix convention."""

    cleaned = str(symbol).strip().upper()
    if not cleaned:
        return cleaned
    if "." not in cleaned and cleaned not in {"^NSEI", "NIFTY50"}:
        return f"{cleaned}.NS"
    return cleaned


class DataIngestion:
    """Handles stock universe loading plus OHLCV fetching and caching."""

    def __init__(
        self,
        cache_dir: str = "./data/cache",
        provider: Optional[OHLCVProvider] = None,
        universe_path: Optional[str] = None,
        max_missing_pct: float = 0.20,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider or YFinanceOHLCVProvider()
        self.universe_path = Path(universe_path) if universe_path else Path("./data/universe/nse_universe.csv")
        self.max_missing_pct = max_missing_pct
        self._universe_cache: Optional[List[UniverseMember]] = None
        logger.info(
            "Initialized DataIngestion with cache=%s provider=%s",
            self.cache_dir,
            self.provider.source_name,
        )

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------

    def get_universe(self) -> List[UniverseMember]:
        """Return CSV-backed universe members, falling back to a built-in list."""

        if self._universe_cache is None:
            loaded = self._load_universe_from_csv(self.universe_path)
            self._universe_cache = loaded or self._default_universe()
        logger.info("NSE universe: %s stocks", len(self._universe_cache))
        return list(self._universe_cache)

    def get_nse_universe(self) -> List[str]:
        """Return NSE symbols with .NS suffix for scanner compatibility."""

        return [member.symbol for member in self.get_universe()]

    def get_sector(self, symbol: str) -> str:
        """Return sector for a symbol when known from the universe file."""

        normalized = normalize_nse_symbol(symbol)
        for member in self.get_universe():
            if member.symbol == normalized:
                return member.sector or "Unknown"
        return "Unknown"

    def _load_universe_from_csv(self, path: Path) -> List[UniverseMember]:
        if not path.exists():
            return []

        try:
            df = pd.read_csv(path)
        except Exception as exc:
            logger.warning("[ingest] Universe CSV read error for %s: %s", path, exc)
            return []

        if "symbol" not in {c.lower() for c in df.columns}:
            logger.warning("[ingest] Universe CSV %s missing 'symbol' column", path)
            return []

        lower_to_actual = {c.lower(): c for c in df.columns}
        symbol_col = lower_to_actual["symbol"]
        name_col = lower_to_actual.get("name")
        sector_col = lower_to_actual.get("sector")

        members: List[UniverseMember] = []
        seen = set()
        for _, row in df.iterrows():
            symbol = normalize_nse_symbol(row.get(symbol_col, ""))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            members.append(
                UniverseMember(
                    symbol=symbol,
                    name=str(row.get(name_col, "")).strip() if name_col else "",
                    sector=str(row.get(sector_col, "Unknown")).strip() if sector_col else "Unknown",
                )
            )
        return members

    def _default_universe(self) -> List[UniverseMember]:
        rows = [
            ("RELIANCE.NS", "Reliance Industries", "Energy"),
            ("TCS.NS", "Tata Consultancy Services", "IT"),
            ("HDFCBANK.NS", "HDFC Bank", "Financial Services"),
            ("INFY.NS", "Infosys", "IT"),
            ("HINDUNILVR.NS", "Hindustan Unilever", "FMCG"),
            ("ICICIBANK.NS", "ICICI Bank", "Financial Services"),
            ("KOTAKBANK.NS", "Kotak Mahindra Bank", "Financial Services"),
            ("SBIN.NS", "State Bank of India", "Financial Services"),
            ("BHARTIARTL.NS", "Bharti Airtel", "Telecom"),
            ("ITC.NS", "ITC", "FMCG"),
            ("AXISBANK.NS", "Axis Bank", "Financial Services"),
            ("LT.NS", "Larsen and Toubro", "Construction"),
            ("ASIANPAINT.NS", "Asian Paints", "Consumer Durables"),
            ("MARUTI.NS", "Maruti Suzuki", "Automobile"),
            ("HCLTECH.NS", "HCL Technologies", "IT"),
            ("WIPRO.NS", "Wipro", "IT"),
            ("ULTRACEMCO.NS", "UltraTech Cement", "Cement"),
            ("TITAN.NS", "Titan Company", "Consumer Durables"),
            ("NESTLEIND.NS", "Nestle India", "FMCG"),
            ("BAJFINANCE.NS", "Bajaj Finance", "Financial Services"),
            ("TATAMOTORS.NS", "Tata Motors", "Automobile"),
            ("TATASTEEL.NS", "Tata Steel", "Metals"),
            ("SUNPHARMA.NS", "Sun Pharma", "Pharma"),
            ("ONGC.NS", "ONGC", "Energy"),
            ("M&M.NS", "Mahindra and Mahindra", "Automobile"),
            ("NTPC.NS", "NTPC", "Power"),
            ("POWERGRID.NS", "Power Grid", "Power"),
            ("TECHM.NS", "Tech Mahindra", "IT"),
            ("INDUSINDBK.NS", "IndusInd Bank", "Financial Services"),
            ("ADANIPORTS.NS", "Adani Ports", "Logistics"),
        ]
        return [UniverseMember(*row) for row in rows]

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data, preserving the original DataFrame-only API."""

        return self.fetch_ohlcv_with_quality(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            use_cache=use_cache,
        ).data

    def fetch_ohlcv_with_quality(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> OHLCVFetchResult:
        """Fetch OHLCV data and return metadata about quality/cache/source."""

        normalized = normalize_nse_symbol(symbol)
        end_date = end_date or datetime.now()
        start_date = start_date or (end_date - timedelta(days=260))

        cached: Optional[_CachedOHLCV] = None
        if use_cache:
            cached = self._load_from_cache(normalized, start_date, end_date)

        if cached is not None:
            quality = self._quality_metadata(
                normalized,
                cached.data,
                start_date,
                end_date,
                cache_hit=True,
                cache_age=cached.age,
            )
            return OHLCVFetchResult(normalized, cached.data, quality)

        try:
            logger.info(
                "Fetching %s from %s (%s to %s)",
                normalized,
                self.provider.source_name,
                start_date.date(),
                end_date.date(),
            )
            data = self.provider.fetch(normalized, start_date, end_date)
        except Exception as exc:
            logger.error("Error fetching %s: %s", normalized, exc)
            quality = self._empty_quality(
                normalized,
                start_date,
                end_date,
                error=str(exc),
            )
            return OHLCVFetchResult(normalized, None, quality)

        if data is None or data.empty:
            logger.warning("No data returned for %s", normalized)
            quality = self._empty_quality(
                normalized,
                start_date,
                end_date,
                error="No data returned",
            )
            return OHLCVFetchResult(normalized, None, quality)

        quality = self._quality_metadata(
            normalized,
            data,
            start_date,
            end_date,
            cache_hit=False,
            cache_age=None,
        )

        if quality.missing_data_pct > self.max_missing_pct:
            logger.warning(
                "%s: %.1f%% data missing, skipping",
                normalized,
                quality.missing_data_pct * 100,
            )
            return OHLCVFetchResult(normalized, None, quality)

        if use_cache:
            self._save_to_cache(normalized, data, start_date, end_date)

        logger.debug("Fetched %s bars for %s", len(data), normalized)
        return OHLCVFetchResult(normalized, data, quality)

    def fetch_multiple(
        self,
        symbols: List[str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch OHLCV data for multiple symbols."""

        results: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            data = self.fetch_ohlcv(symbol, start_date, end_date, use_cache)
            if data is not None:
                results[normalize_nse_symbol(symbol)] = data
        logger.info("Fetched %s/%s symbols successfully", len(results), len(symbols))
        return results

    def _quality_metadata(
        self,
        symbol: str,
        data: pd.DataFrame,
        start_date: datetime,
        end_date: datetime,
        cache_hit: bool,
        cache_age: Optional[timedelta],
    ) -> DataQualityMetadata:
        expected_bdays = max(1, len(pd.bdate_range(start=start_date, end=end_date)))
        rows = len(data)
        missing_pct = max(0.0, (expected_bdays - rows) / expected_bdays)
        first = data.index[0] if rows else None
        last = data.index[-1] if rows else None
        return DataQualityMetadata(
            symbol=symbol,
            source=self.provider.source_name,
            adjusted=self.provider.adjusted,
            rows=rows,
            expected_business_days=expected_bdays,
            missing_data_pct=round(missing_pct, 4),
            cache_hit=cache_hit,
            cache_age_seconds=round(cache_age.total_seconds(), 2) if cache_age else None,
            start_date=start_date.date().isoformat(),
            end_date=end_date.date().isoformat(),
            first_bar_date=first.date().isoformat() if first is not None else None,
            last_bar_date=last.date().isoformat() if last is not None else None,
        )

    def _empty_quality(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        error: str,
    ) -> DataQualityMetadata:
        expected_bdays = max(1, len(pd.bdate_range(start=start_date, end=end_date)))
        return DataQualityMetadata(
            symbol=symbol,
            source=self.provider.source_name,
            adjusted=self.provider.adjusted,
            rows=0,
            expected_business_days=expected_bdays,
            missing_data_pct=1.0,
            start_date=start_date.date().isoformat(),
            end_date=end_date.date().isoformat(),
            error=error,
        )

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_filename(self, symbol: str, start_date: datetime, end_date: datetime) -> Path:
        clean_symbol = symbol.replace(".", "_").replace("^", "")
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        return self.cache_dir / f"{clean_symbol}_{start_str}_{end_str}.parquet"

    def _load_from_cache(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Optional[_CachedOHLCV]:
        cache_file = self._cache_filename(symbol, start_date, end_date)
        if not cache_file.exists():
            return None

        cache_age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if cache_age >= timedelta(days=1):
            return None

        try:
            return _CachedOHLCV(pd.read_parquet(cache_file), cache_age)
        except Exception as exc:
            logger.warning("Cache read error for %s: %s", symbol, exc)
            return None

    def _save_to_cache(
        self,
        symbol: str,
        data: pd.DataFrame,
        start_date: datetime,
        end_date: datetime,
    ) -> None:
        cache_file = self._cache_filename(symbol, start_date, end_date)
        try:
            data.to_parquet(cache_file)
            logger.debug("Cached %s to %s", symbol, cache_file.name)
        except Exception as exc:
            logger.warning("Cache write error for %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # Fundamentals compatibility
    # ------------------------------------------------------------------

    def fetch_fundamentals(self, symbol: str) -> dict:
        """Fetch fundamental data through the CSV/yfinance fundamental module."""

        from modules.fundamental import FundamentalFilter

        result = FundamentalFilter().screen(symbol)
        data = result.data
        return {
            "market_cap_cr": data.market_cap_cr,
            "revenue_growth_pct": data.revenue_growth_pct,
            "debt_to_equity": data.debt_to_equity,
            "promoter_holding_pct": data.promoter_holding_pct,
            "source": data.source,
            "as_of": data.as_of,
            "sector": data.sector,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    ingestion = DataIngestion()
    print("\n=== Testing single symbol fetch ===")
    fetched = ingestion.fetch_ohlcv_with_quality("RELIANCE.NS")
    if fetched.data is not None:
        print(f"Fetched {len(fetched.data)} bars for RELIANCE.NS")
        print(fetched.quality.to_dict())
        print(fetched.data.tail())
