"""
Data Ingestion Module
Fetches OHLCV data from NSE/BSE using yfinance and NSEpy
Implements caching to avoid repeated API calls
"""

import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class DataIngestion:
    """Handles fetching and caching of stock market data"""

    def __init__(self, cache_dir: str = "./data/cache"):
        """
        Initialize data ingestion module

        Args:
            cache_dir: Directory for caching downloaded data
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized DataIngestion with cache: {self.cache_dir}")

    def get_nse_universe(self) -> List[str]:
        """
        Get list of NSE stock symbols

        Returns:
            List of stock symbols with .NS suffix
        """
        # For MVP, we'll use a curated list of liquid stocks
        # In production, fetch from NSE website or use nsetools
        nse_stocks = [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "HINDUNILVR.NS",
            "ICICIBANK.NS", "KOTAKBANK.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS",
            "AXISBANK.NS", "LT.NS", "ASIANPAINT.NS", "MARUTI.NS", "HCLTECH.NS",
            "WIPRO.NS", "ULTRACEMCO.NS", "TITAN.NS", "NESTLEIND.NS", "BAJFINANCE.NS",
            "TATAMOTORS.NS", "TATASTEEL.NS", "SUNPHARMA.NS", "ONGC.NS", "M&M.NS",
            "NTPC.NS", "POWERGRID.NS", "TECHM.NS", "INDUSINDBK.NS", "ADANIPORTS.NS"
        ]
        logger.info(f"NSE universe: {len(nse_stocks)} stocks")
        return nse_stocks

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data for a symbol

        Args:
            symbol: Stock symbol (e.g., 'RELIANCE.NS')
            start_date: Start date for data fetch (default: 100 days ago)
            end_date: End date for data fetch (default: today)
            use_cache: Whether to use cached data

        Returns:
            DataFrame with OHLCV data or None if fetch fails
        """
        # Set default dates
        if end_date is None:
            end_date = datetime.now()
        if start_date is None:
            # 150 calendar days ≈ 105 trading days — enough for all pattern lookbacks
            start_date = end_date - timedelta(days=150)

        # Check cache first
        if use_cache:
            cached_data = self._load_from_cache(symbol, start_date, end_date)
            if cached_data is not None:
                logger.debug(f"Cache hit for {symbol}")
                return cached_data

        # Fetch from yfinance
        try:
            logger.info(f"Fetching {symbol} from yfinance ({start_date.date()} to {end_date.date()})")
            ticker = yf.Ticker(symbol)
            data = ticker.history(start=start_date, end=end_date)

            if data.empty:
                logger.warning(f"No data returned for {symbol}")
                return None

            # Calculate missing data percentage using business days
            # (trading days ≈ weekdays; NSE also has ~15 holidays/year)
            expected_bdays = max(
                1,
                len(pd.bdate_range(start=start_date, end=end_date))
            )
            actual_days = len(data)
            # Allow up to 20% missing vs expected business days
            # (accounts for NSE-specific holidays not in pandas bdate_range)
            missing_pct = max(0.0, (expected_bdays - actual_days) / expected_bdays)

            if missing_pct > 0.20:  # More than 20% missing vs business days
                logger.warning(f"{symbol}: {missing_pct:.1%} data missing, skipping")
                return None

            # Cache the data
            self._save_to_cache(symbol, data, start_date, end_date)

            logger.debug(f"Fetched {len(data)} bars for {symbol}")
            return data

        except Exception as e:
            logger.error(f"Error fetching {symbol}: {str(e)}")
            return None

    def fetch_multiple(
        self,
        symbols: List[str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True
    ) -> dict:
        """
        Fetch OHLCV data for multiple symbols

        Args:
            symbols: List of stock symbols
            start_date: Start date for data fetch
            end_date: End date for data fetch
            use_cache: Whether to use cached data

        Returns:
            Dictionary mapping symbol to DataFrame
        """
        results = {}

        for symbol in symbols:
            data = self.fetch_ohlcv(symbol, start_date, end_date, use_cache)
            if data is not None:
                results[symbol] = data

        logger.info(f"Fetched {len(results)}/{len(symbols)} symbols successfully")
        return results

    def _cache_filename(self, symbol: str, start_date: datetime, end_date: datetime) -> Path:
        """Generate cache filename"""
        clean_symbol = symbol.replace(".", "_")
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        return self.cache_dir / f"{clean_symbol}_{start_str}_{end_str}.parquet"

    def _load_from_cache(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> Optional[pd.DataFrame]:
        """Load data from cache if available"""
        cache_file = self._cache_filename(symbol, start_date, end_date)

        if cache_file.exists():
            # Check if cache is less than 1 day old
            cache_age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            if cache_age < timedelta(days=1):
                try:
                    return pd.read_parquet(cache_file)
                except Exception as e:
                    logger.warning(f"Cache read error for {symbol}: {e}")

        return None

    def _save_to_cache(
        self,
        symbol: str,
        data: pd.DataFrame,
        start_date: datetime,
        end_date: datetime
    ):
        """Save data to cache"""
        cache_file = self._cache_filename(symbol, start_date, end_date)
        try:
            data.to_parquet(cache_file)
            logger.debug(f"Cached {symbol} to {cache_file.name}")
        except Exception as e:
            logger.warning(f"Cache write error for {symbol}: {e}")

    def fetch_fundamentals(self, symbol: str) -> dict:
        """
        Fetch fundamental data for a symbol

        Note: This is a placeholder. In production, use Screener.in API or CSV exports

        Returns:
            Dictionary with fundamental metrics
        """
        # Placeholder - return mock data
        # In production, integrate with Screener.in API
        return {
            "market_cap_cr": 150000,  # ₹1.5 Lakh Crore
            "revenue_growth_yoy": 0.12,  # 12%
            "debt_to_equity": 0.45,
            "promoter_holding": 0.65,  # 65%
            "pat_ttm": 5000  # ₹5000 Crore
        }


if __name__ == "__main__":
    # Test the data ingestion module
    logging.basicConfig(level=logging.INFO)

    ingestion = DataIngestion()

    # Test single symbol fetch
    print("\n=== Testing single symbol fetch ===")
    data = ingestion.fetch_ohlcv("RELIANCE.NS")
    if data is not None:
        print(f"Fetched {len(data)} bars for RELIANCE.NS")
        print(data.tail())

    # Test multiple symbols
    print("\n=== Testing multiple symbols fetch ===")
    symbols = ["TCS.NS", "INFY.NS", "HDFCBANK.NS"]
    results = ingestion.fetch_multiple(symbols)
    print(f"Successfully fetched: {list(results.keys())}")
