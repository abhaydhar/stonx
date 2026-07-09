"""
Data Tools — LangChain/CrewAI @tool wrappers around DataIngestion
and FundamentalFilter modules.

Each tool:
  - Accepts a plain string argument (agent sends JSON string or plain text)
  - Returns a plain string (the agent reads and reasons about it)
  - Handles errors gracefully (returns error description rather than raising)
"""

import json
import logging
from datetime import datetime, timedelta

from langchain.tools import tool

from modules.scanner import (
    build_fundamental_filter_from_config,
    build_ingestion_from_config,
    load_scanner_config,
)

logger = logging.getLogger(__name__)

# Singletons re-used across tool calls to preserve cache
_config = load_scanner_config()
_ingestion = build_ingestion_from_config(_config)
_fundamental = build_fundamental_filter_from_config(_config)


# ---------------------------------------------------------------------------
# fetch_ohlcv_tool
# ---------------------------------------------------------------------------

@tool
def fetch_ohlcv_tool(symbol: str) -> str:
    """
    Fetch the last 100 days of OHLCV (Open, High, Low, Close, Volume) data
    for a single NSE/BSE stock symbol (e.g. 'RELIANCE.NS').

    Returns a JSON string with shape summary and last 5 rows, or an error
    message if the fetch fails.

    Input: stock symbol string like 'TCS.NS'
    """
    symbol = symbol.strip().strip("'\"")
    df = _ingestion.fetch_ohlcv(symbol)

    if df is None or df.empty:
        return json.dumps({"error": f"No data available for {symbol}"})

    result = {
        "symbol": symbol,
        "rows": len(df),
        "columns": list(df.columns),
        "date_range": {
            "start": str(df.index[0].date()),
            "end": str(df.index[-1].date()),
        },
        "latest": {
            "date": str(df.index[-1].date()),
            "open": round(float(df["Open"].iloc[-1]), 2),
            "high": round(float(df["High"].iloc[-1]), 2),
            "low": round(float(df["Low"].iloc[-1]), 2),
            "close": round(float(df["Close"].iloc[-1]), 2),
            "volume": int(df["Volume"].iloc[-1]),
        },
        "price_change_pct_100d": round(
            (float(df["Close"].iloc[-1]) - float(df["Close"].iloc[0]))
            / float(df["Close"].iloc[0])
            * 100,
            2,
        ),
    }
    return json.dumps(result)


# ---------------------------------------------------------------------------
# fetch_universe_tool
# ---------------------------------------------------------------------------

@tool
def fetch_universe_tool(dummy_input: str = "") -> str:
    """
    Return the current NSE stock universe being scanned (list of symbols).

    No input required — pass any string or leave empty.
    Returns a JSON list of symbol strings.
    """
    symbols = _ingestion.get_nse_universe()
    return json.dumps({"universe": symbols, "count": len(symbols)})


# ---------------------------------------------------------------------------
# screen_fundamentals_tool
# ---------------------------------------------------------------------------

@tool
def screen_fundamentals_tool(symbol: str) -> str:
    """
    Apply fundamental filters to a single NSE/BSE stock symbol.

    Checks:
      - Market cap >= 500 Cr
      - Revenue growth >= 0%
      - Debt-to-equity <= 1.0
      - Promoter holding >= 40% (skipped if data not available)

    Input: stock symbol string like 'INFY.NS'
    Returns: JSON with 'passed' (bool) and filter details.
    """
    symbol = symbol.strip().strip("'\"")

    try:
        result = _fundamental.screen(symbol)
    except Exception as exc:
        return json.dumps({"symbol": symbol, "error": str(exc)})

    data = result.data
    output = {
        "symbol": symbol,
        "passed": result.passed,
        "rejection_reason": result.rejection_reason,
        "fundamentals": {
            "market_cap_cr": round(data.market_cap_cr, 0) if data.market_cap_cr else None,
            "revenue_growth_pct": round(data.revenue_growth_pct, 1) if data.revenue_growth_pct is not None else None,
            "debt_to_equity": round(data.debt_to_equity, 2) if data.debt_to_equity is not None else None,
            "promoter_holding_pct": round(data.promoter_holding_pct * 100, 1) if data.promoter_holding_pct is not None else None,
            "sector": data.sector,
            "source": data.source,
            "as_of": data.as_of,
        },
    }
    return json.dumps(output)
