"""
Stock Review: multi-agent, on-demand deep dive for a single symbol.

Seven deterministic "agents" (plain Python, no LLM) run concurrently against
one symbol and are consolidated into a single report, plus an optional 8th
LLM-powered synthesis layer:

    1. financial_report  -- key company metrics (yfinance ``info``)
    2. pnl_checker        -- income-statement P&L trend (yfinance ``financials``)
    3. candlesticks        -- last N months OHLCV (yfinance history)
    4. pattern_recognizer  -- reuses modules.patterns.PatternDetector
    5. news_validation     -- recent headlines (yfinance ``news``) + freshness/keyword tally
    6. ema_vwap            -- EMA20/EMA50 vs VWAP trend bias
    7. rsi                 -- 14-period RSI overbought/oversold check
    8. analyst_verdict     -- LLM synthesis of the other 7 (only if configured)

Each agent function never raises: failures degrade to
``{"status": "error", "error": "..."}`` so one bad data source doesn't take
down the whole review. ``run_stock_review`` fetches OHLCV once (shared by
candlesticks/pattern/ema_vwap/rsi) and fans the remaining network calls
(financial_report, pnl_checker, news_validation) out across a thread pool,
since the work is I/O-bound (HTTP calls to Yahoo Finance) and independent
per agent -- not LLM reasoning, so a plain thread pool is the right tool.

The LLM layer (``modules.llm_client``, wired to Gemini's OpenAI-compatible
endpoint) is entirely optional: financial_report gets a narrative field and
news_validation gets a real sentiment read when GEMINI_API_KEY is set, and
the consolidated analyst_verdict agent only runs at all when it's set. With
no key, every agent still returns full structured data -- this module was
deterministic-first before the LLM layer existed and stays that way.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from modules import llm_client
from modules.ingest import DataIngestion, normalize_nse_symbol
from modules.patterns import PatternDetector

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL_LIST_PATH = Path("./data/universe/nse_all_equities.csv")


# ---------------------------------------------------------------------------
# Symbol universe (for the dashboard's autocomplete)
# ---------------------------------------------------------------------------


def load_symbol_universe(path: Path | str = DEFAULT_SYMBOL_LIST_PATH) -> pd.DataFrame:
    """Return the cached NSE symbol/name list, or an empty frame if missing.

    Columns: symbol (with .NS suffix), name, series. Never raises.
    """
    try:
        file_path = Path(path)
        if not file_path.is_file():
            return pd.DataFrame(columns=["symbol", "name", "series"])
        df = pd.read_csv(file_path)
        return df.reindex(columns=["symbol", "name", "series"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[stock_review] failed to load symbol universe %s: %s", path, exc)
        return pd.DataFrame(columns=["symbol", "name", "series"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _error(agent: str, exc: Exception) -> Dict[str, Any]:
    return {"agent": agent, "status": "error", "error": str(exc)}


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Agent 1: Financial report
# ---------------------------------------------------------------------------


def financial_report_agent(symbol: str) -> Dict[str, Any]:
    """Key company metrics from yfinance's ``info`` payload."""
    try:
        info = yf.Ticker(symbol).info or {}
        metrics = {
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "current_price": _num(info.get("currentPrice")),
            "market_cap": _num(info.get("marketCap")),
            "trailing_pe": _num(info.get("trailingPE")),
            "forward_pe": _num(info.get("forwardPE")),
            "trailing_eps": _num(info.get("trailingEps")),
            "return_on_equity": _num(info.get("returnOnEquity")),
            "revenue_growth": _num(info.get("revenueGrowth")),
            "debt_to_equity": _num(info.get("debtToEquity")),
            "dividend_yield": _num(info.get("dividendYield")),
            "fifty_two_week_high": _num(info.get("fiftyTwoWeekHigh")),
            "fifty_two_week_low": _num(info.get("fiftyTwoWeekLow")),
        }
        if not any(v is not None for v in metrics.values()):
            return {"agent": "financial_report", "status": "no_data", "data": {}}
        metrics["narrative"] = _financial_narrative(metrics)
        return {"agent": "financial_report", "status": "ok", "data": metrics}
    except Exception as exc:  # noqa: BLE001
        return _error("financial_report", exc)


def _financial_narrative(metrics: Dict[str, Any]) -> Optional[str]:
    """LLM synthesis of the metrics dict into a short analyst-style read. None if no LLM configured."""
    if not llm_client.is_configured():
        return None
    system = (
        "You are an equity research analyst. Given a company's key financial metrics as "
        "JSON, write a concise 3-4 sentence factual summary of financial health, valuation, "
        "and growth trajectory. State facts and ratios plainly. Do not give buy/sell/hold "
        "advice or price targets."
    )
    return llm_client.complete(system, json.dumps(metrics, default=str))


# ---------------------------------------------------------------------------
# Agent 2: P&L checker (income-statement trend)
# ---------------------------------------------------------------------------


def pnl_checker_agent(symbol: str) -> Dict[str, Any]:
    """Annual revenue/net-income trend from yfinance's income statement."""
    try:
        financials = yf.Ticker(symbol).financials
        if financials is None or financials.empty:
            return {"agent": "pnl_checker", "status": "no_data", "data": {}}

        rows = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]
        periods = sorted(financials.columns, reverse=True)
        history: List[Dict[str, Any]] = []
        for period in periods:
            entry = {"period": period.strftime("%Y-%m-%d")}
            for row in rows:
                entry[row] = _num(financials.loc[row, period]) if row in financials.index else None
            history.append(entry)

        revenue_growth_pct = None
        net_margin_pct = None
        if len(history) >= 2:
            latest, prior = history[0], history[1]
            rev_latest, rev_prior = latest.get("Total Revenue"), prior.get("Total Revenue")
            if rev_latest is not None and rev_prior:
                revenue_growth_pct = (rev_latest - rev_prior) / abs(rev_prior) * 100.0
        if history:
            rev_latest = history[0].get("Total Revenue")
            net_latest = history[0].get("Net Income")
            if rev_latest and net_latest is not None:
                net_margin_pct = net_latest / rev_latest * 100.0

        return {
            "agent": "pnl_checker",
            "status": "ok",
            "data": {
                "history": history,
                "revenue_growth_pct": revenue_growth_pct,
                "net_margin_pct": net_margin_pct,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error("pnl_checker", exc)


# ---------------------------------------------------------------------------
# Agent 3: Candlesticks (OHLCV history)
# ---------------------------------------------------------------------------


def candlestick_agent(symbol: str, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Summarize the shared OHLCV window; the caller supplies the data."""
    try:
        if df is None or df.empty:
            return {"agent": "candlesticks", "status": "no_data", "data": {}}

        closes = df["Close"]
        period_change_pct = (
            (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100.0 if closes.iloc[0] else None
        )
        candles = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": _num(row["Open"]),
                "high": _num(row["High"]),
                "low": _num(row["Low"]),
                "close": _num(row["Close"]),
                "volume": _num(row["Volume"]),
            }
            for idx, row in df.iterrows()
        ]
        return {
            "agent": "candlesticks",
            "status": "ok",
            "data": {
                "bars": len(df),
                "period_high": _num(df["High"].max()),
                "period_low": _num(df["Low"].min()),
                "period_change_pct": _num(period_change_pct),
                "avg_daily_volume": _num(df["Volume"].mean()),
                "candles": candles,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error("candlesticks", exc)


# ---------------------------------------------------------------------------
# Agent 4: Pattern recognizer (reuses modules.patterns)
# ---------------------------------------------------------------------------


def pattern_recognizer_agent(symbol: str, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Run the existing consolidation/higher-lows/range-tightening detectors."""
    try:
        if df is None or df.empty:
            return {"agent": "pattern_recognizer", "status": "no_data", "data": {}}
        scan = PatternDetector().scan(symbol, df)
        return {
            "agent": "pattern_recognizer",
            "status": "ok",
            "data": {
                "best_pattern": scan.best_pattern,
                "passed": scan.passed,
                "patterns": [asdict(p) for p in scan.patterns],
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error("pattern_recognizer", exc)


# ---------------------------------------------------------------------------
# Agent 5: Real-world news validation
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = {
    "beat", "beats", "growth", "surge", "rally", "upgrade", "profit", "gain",
    "record", "expansion", "strong", "outperform", "bullish", "wins", "win",
}
_NEGATIVE_WORDS = {
    "miss", "misses", "downgrade", "loss", "losses", "fall", "falls", "plunge",
    "decline", "weak", "underperform", "bearish", "probe", "lawsuit", "fraud",
    "layoff", "layoffs", "cut", "cuts",
}


def _keyword_tone(text: str) -> str:
    words = set(text.lower().split())
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _parse_pub_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _news_llm_sentiment(symbol: str, articles: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """LLM read of overall news sentiment from headlines. None if no LLM configured."""
    if not llm_client.is_configured():
        return None
    headlines = "\n".join(f"- {a['title']}" for a in articles[:10] if a.get("title"))
    if not headlines:
        return None
    system = (
        "You are an equity news analyst. Given recent headlines about a stock, assess the "
        "overall real-world news sentiment. Respond in exactly this two-line format:\n"
        "Label: <bullish|bearish|neutral>\n"
        "Rationale: <one concise sentence>"
    )
    raw = llm_client.complete(system, f"Symbol: {symbol}\nHeadlines:\n{headlines}", max_tokens=150)
    if not raw:
        return None

    label, rationale = "neutral", raw
    for line in raw.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("label:"):
            candidate = lowered.split(":", 1)[1].strip()
            if candidate in {"bullish", "bearish", "neutral"}:
                label = candidate
        elif lowered.startswith("rationale:"):
            rationale = stripped.split(":", 1)[1].strip()
    return {"label": label, "rationale": rationale}


def news_validation_agent(symbol: str, lookback_days: int = 30) -> Dict[str, Any]:
    """Recent headlines, a naive keyword-based tone tally, and an optional LLM sentiment read."""
    try:
        raw = yf.Ticker(symbol).news or []
        articles: List[Dict[str, Any]] = []
        recent_count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for item in raw:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = content.get("title") or ""
            pub_date = _parse_pub_date(content.get("pubDate"))
            if pub_date is not None and pub_date >= cutoff:
                recent_count += 1
            provider = (content.get("provider") or {}).get("displayName")
            url = (content.get("canonicalUrl") or {}).get("url")
            tone = _keyword_tone(f"{title} {content.get('summary', '')}")
            articles.append(
                {
                    "title": title,
                    "provider": provider,
                    "published": pub_date.isoformat() if pub_date else content.get("pubDate"),
                    "url": url,
                    "tone": tone,
                }
            )

        tone_counts = {"positive": 0, "negative": 0, "neutral": 0}
        for a in articles:
            tone_counts[a["tone"]] += 1

        return {
            "agent": "news_validation",
            "status": "ok" if articles else "no_data",
            "data": {
                "total_articles": len(articles),
                "recent_articles": recent_count,
                "lookback_days": lookback_days,
                "tone_counts": tone_counts,
                "llm_sentiment": _news_llm_sentiment(symbol, articles),
                "articles": articles[:10],
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error("news_validation", exc)


# ---------------------------------------------------------------------------
# Agent 6: EMA + VWAP trend check
# ---------------------------------------------------------------------------


def ema_vwap_agent(symbol: str, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """EMA20/EMA50 alignment vs price, and price vs session VWAP."""
    try:
        if df is None or len(df) < 20:
            return {"agent": "ema_vwap", "status": "no_data", "data": {}}

        close = df["Close"]
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1] if len(df) >= 50 else None
        current_price = close.iloc[-1]

        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        vwap = (typical_price * df["Volume"]).sum() / df["Volume"].sum() if df["Volume"].sum() else None

        if ema50 is not None:
            if current_price > ema20 > ema50:
                trend_bias = "bullish"
            elif current_price < ema20 < ema50:
                trend_bias = "bearish"
            else:
                trend_bias = "mixed"
        else:
            trend_bias = "bullish" if current_price > ema20 else "bearish"

        vwap_position = None
        if vwap is not None:
            vwap_position = "above_vwap" if current_price > vwap else "below_vwap"

        return {
            "agent": "ema_vwap",
            "status": "ok",
            "data": {
                "current_price": _num(current_price),
                "ema20": _num(ema20),
                "ema50": _num(ema50),
                "vwap": _num(vwap),
                "trend_bias": trend_bias,
                "vwap_position": vwap_position,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _error("ema_vwap", exc)


# ---------------------------------------------------------------------------
# Agent 7: RSI
# ---------------------------------------------------------------------------


def rsi_agent(symbol: str, df: Optional[pd.DataFrame], period: int = 14) -> Dict[str, Any]:
    """14-period RSI with a standard overbought/oversold classification."""
    try:
        if df is None or len(df) < period + 1:
            return {"agent": "rsi", "status": "no_data", "data": {}}

        delta = df["Close"].diff()
        gain = delta.clip(lower=0).rolling(window=period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        # A zero-loss window (pure uptrend) makes rs -> inf, i.e. RSI = 100; a
        # zero-gain-and-zero-loss window (flat) is conventionally RSI = 50.
        zero_loss = loss == 0
        rsi_series = rsi_series.mask(zero_loss & (gain > 0), 100.0)
        rsi_series = rsi_series.mask(zero_loss & (gain == 0), 50.0)
        rsi_value = rsi_series.iloc[-1]

        if pd.isna(rsi_value):
            return {"agent": "rsi", "status": "no_data", "data": {}}

        if rsi_value >= 70:
            classification = "overbought"
        elif rsi_value <= 30:
            classification = "oversold"
        else:
            classification = "neutral"

        return {
            "agent": "rsi",
            "status": "ok",
            "data": {"rsi": _num(rsi_value), "period": period, "classification": classification},
        }
    except Exception as exc:  # noqa: BLE001
        return _error("rsi", exc)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_BULLISH_STATUSES = {"bullish", "above_vwap", "oversold"}
_BEARISH_STATUSES = {"bearish", "below_vwap", "overbought"}


def _consensus(agents: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Tally simple directional signals across the technical agents.

    Purely a count of bullish/bearish/neutral technical signals -- not a
    recommendation. Fundamental/news agents are informational only and are
    not counted here since they don't carry a clean directional signal.
    """
    bullish = bearish = neutral = 0

    ema_vwap = agents.get("ema_vwap", {}).get("data", {})
    if ema_vwap.get("trend_bias") == "bullish":
        bullish += 1
    elif ema_vwap.get("trend_bias") == "bearish":
        bearish += 1
    elif ema_vwap.get("trend_bias") == "mixed":
        neutral += 1
    if ema_vwap.get("vwap_position") == "above_vwap":
        bullish += 1
    elif ema_vwap.get("vwap_position") == "below_vwap":
        bearish += 1

    rsi = agents.get("rsi", {}).get("data", {})
    if rsi.get("classification") == "oversold":
        bullish += 1
    elif rsi.get("classification") == "overbought":
        bearish += 1
    elif rsi.get("classification") == "neutral":
        neutral += 1

    pattern = agents.get("pattern_recognizer", {}).get("data", {})
    if pattern.get("passed"):
        bullish += 1

    return {"bullish_signals": bullish, "bearish_signals": bearish, "neutral_signals": neutral}


def _trim_for_prompt(agents: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Strip bulky fields (candles, full article lists) before sending to the LLM."""
    trimmed: Dict[str, Any] = {}
    for name, agent in agents.items():
        data = dict(agent.get("data") or {})
        if name == "candlesticks":
            data.pop("candles", None)
        if name == "news_validation":
            data["articles"] = [a.get("title") for a in (data.get("articles") or [])[:5]]
        if name == "pattern_recognizer":
            data["patterns"] = [
                {"pattern_name": p.get("pattern_name"), "detected": p.get("detected")}
                for p in (data.get("patterns") or [])
            ]
        trimmed[name] = {"status": agent.get("status"), "data": data}
    return trimmed


def analyst_verdict_agent(
    symbol: str,
    agents: Dict[str, Dict[str, Any]],
    consensus: Dict[str, Any],
) -> Dict[str, Any]:
    """LLM synthesis of all other agents into one narrative. Only runs if configured.

    Returns ``{"status": "unavailable"}`` (not an error) when no LLM is
    configured -- this is an expected, normal state, not a failure.
    """
    if not llm_client.is_configured():
        return {"agent": "analyst_verdict", "status": "unavailable", "data": {}}
    try:
        system = (
            "You are a senior equity research analyst producing an internal briefing. You "
            "are given structured output from seven analytical checks (fundamentals, "
            "P&L trend, candlestick data, chart pattern detection, recent news, EMA/VWAP "
            "trend, RSI) for one stock, as JSON. Write a briefing with exactly these "
            "sections:\n"
            "Summary: <3-4 sentences synthesizing the fundamentals, technicals, and news>\n"
            "Key risks: <1-2 sentences>\n"
            "Stance: <bullish|bearish|neutral> -- <one sentence justification>\n"
            "This is informational analysis only, not investment advice; do not suggest "
            "position sizing, entry/exit prices, or a buy/sell/hold action."
        )
        user = json.dumps(
            {"symbol": symbol, "consensus": consensus, "agents": _trim_for_prompt(agents)},
            default=str,
        )
        narrative = llm_client.complete(system, user, max_tokens=500)
        if not narrative:
            return {"agent": "analyst_verdict", "status": "error", "error": "LLM returned no content"}
        return {"agent": "analyst_verdict", "status": "ok", "data": {"narrative": narrative}}
    except Exception as exc:  # noqa: BLE001
        return _error("analyst_verdict", exc)


def run_stock_review(
    symbol: str,
    months: int = 6,
    max_workers: int = 6,
    ingestion: Optional[DataIngestion] = None,
) -> Dict[str, Any]:
    """Run all seven agents for one symbol and return a consolidated report.

    OHLCV is fetched once (shared by candlesticks/pattern/ema_vwap/rsi); the
    three remaining network-bound agents (financial_report, pnl_checker,
    news_validation) run concurrently in a thread pool since each is an
    independent HTTP call with no shared state.
    """
    normalized = normalize_nse_symbol(symbol)
    ingestion = ingestion or DataIngestion()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)
    fetched = ingestion.fetch_ohlcv_with_quality(normalized, start_date=start_date, end_date=end_date)
    df = fetched.data

    concurrent_tasks = {
        "financial_report": lambda: financial_report_agent(normalized),
        "pnl_checker": lambda: pnl_checker_agent(normalized),
        "news_validation": lambda: news_validation_agent(normalized),
    }

    agents: Dict[str, Dict[str, Any]] = {
        "candlesticks": candlestick_agent(normalized, df),
        "pattern_recognizer": pattern_recognizer_agent(normalized, df),
        "ema_vwap": ema_vwap_agent(normalized, df),
        "rsi": rsi_agent(normalized, df),
    }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {name: executor.submit(fn) for name, fn in concurrent_tasks.items()}
        for name, future in futures.items():
            try:
                agents[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - defensive; agents shouldn't raise
                agents[name] = _error(name, exc)

    consensus = _consensus(agents)
    # Sequential by nature: this agent needs every other agent's output, so it
    # can only run after the thread pool above has finished.
    verdict = analyst_verdict_agent(normalized, agents, consensus)

    return {
        "symbol": normalized,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "data_quality": fetched.quality.to_dict(),
        "agents": agents,
        "consensus": consensus,
        "analyst_verdict": verdict,
    }
