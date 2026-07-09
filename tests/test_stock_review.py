"""Tests for modules.stock_review (UI-09).

Technical/computation agents are tested with synthetic OHLCV fixtures so no
network calls are made. Network-backed agents (financial_report, pnl_checker,
news_validation) mock ``yf.Ticker`` directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from modules import stock_review as sr


def _make_ohlcv(n: int = 120, trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data (mirrors tests/test_scanner.py's fixture)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")

    if trend == "up":
        close = 1000.0 + np.cumsum(rng.normal(2, 5, n))
    elif trend == "down":
        close = 2000.0 - np.cumsum(rng.normal(2, 5, n))
    else:
        close = 1500.0 + rng.uniform(-30, 30, n)

    noise = rng.uniform(5, 20, n)
    return pd.DataFrame(
        {
            "Open": close - rng.uniform(1, 5, n),
            "High": close + noise,
            "Low": close - noise,
            "Close": close,
            "Volume": rng.integers(500_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )


class TestCandlestickAgent:
    def test_summarizes_shared_dataframe(self):
        df = _make_ohlcv(n=60, trend="up")
        result = sr.candlestick_agent("TEST.NS", df)
        assert result["status"] == "ok"
        data = result["data"]
        assert data["bars"] == 60
        assert len(data["candles"]) == 60
        assert data["period_high"] >= data["period_low"]

    def test_no_data_on_empty_frame(self):
        result = sr.candlestick_agent("TEST.NS", pd.DataFrame())
        assert result["status"] == "no_data"

    def test_no_data_on_none(self):
        assert sr.candlestick_agent("TEST.NS", None)["status"] == "no_data"


class TestPatternRecognizerAgent:
    def test_runs_existing_detector(self):
        df = _make_ohlcv(n=90, trend="up")
        result = sr.pattern_recognizer_agent("TEST.NS", df)
        assert result["status"] == "ok"
        assert "best_pattern" in result["data"]
        assert isinstance(result["data"]["patterns"], list)
        assert len(result["data"]["patterns"]) == 3

    def test_no_data_on_empty_frame(self):
        assert sr.pattern_recognizer_agent("TEST.NS", pd.DataFrame())["status"] == "no_data"


class TestEmaVwapAgent:
    def test_bullish_alignment(self):
        n = 80
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        close = np.linspace(100, 200, n)  # steady uptrend -> price above both EMAs
        df = pd.DataFrame(
            {
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": np.full(n, 1_000_000.0),
            },
            index=dates,
        )
        result = sr.ema_vwap_agent("TEST.NS", df)
        assert result["status"] == "ok"
        assert result["data"]["trend_bias"] == "bullish"
        assert result["data"]["vwap_position"] == "above_vwap"

    def test_no_data_when_too_short(self):
        df = _make_ohlcv(n=5)
        assert sr.ema_vwap_agent("TEST.NS", df)["status"] == "no_data"


class TestRsiAgent:
    def test_classifies_overbought_on_pure_uptrend(self):
        n = 40
        dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        close = np.linspace(100, 300, n)  # monotonic gains -> RSI near 100
        df = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": np.full(n, 1.0)},
            index=dates,
        )
        result = sr.rsi_agent("TEST.NS", df)
        assert result["status"] == "ok"
        assert result["data"]["classification"] == "overbought"
        assert result["data"]["rsi"] > 70

    def test_no_data_when_too_short(self):
        df = _make_ohlcv(n=5)
        assert sr.rsi_agent("TEST.NS", df)["status"] == "no_data"


class TestKeywordTone:
    def test_positive_words_win(self):
        assert sr._keyword_tone("Company beats estimates with record profit") == "positive"

    def test_negative_words_win(self):
        assert sr._keyword_tone("Company misses estimates amid fraud probe") == "negative"

    def test_neutral_when_no_signal_words(self):
        assert sr._keyword_tone("Company announces quarterly results") == "neutral"


class TestParsePubDate:
    def test_parses_zulu_timestamp(self):
        parsed = sr._parse_pub_date("2026-06-22T05:09:16Z")
        assert parsed is not None
        assert parsed.year == 2026

    def test_none_on_missing_or_bad_input(self):
        assert sr._parse_pub_date(None) is None
        assert sr._parse_pub_date("not-a-date") is None


class TestFinancialReportAgent:
    def test_maps_info_fields(self):
        fake_info = {
            "longName": "Test Corp",
            "sector": "IT",
            "currentPrice": 100.0,
            "marketCap": 1_000_000,
            "trailingPE": 20.0,
        }
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(info=fake_info)):
            result = sr.financial_report_agent("TEST.NS")
        assert result["status"] == "ok"
        assert result["data"]["name"] == "Test Corp"
        assert result["data"]["market_cap"] == 1_000_000.0

    def test_no_data_on_empty_info(self):
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(info={})):
            result = sr.financial_report_agent("TEST.NS")
        assert result["status"] == "no_data"

    def test_error_status_never_raises(self):
        with patch.object(sr.yf, "Ticker", side_effect=RuntimeError("network down")):
            result = sr.financial_report_agent("TEST.NS")
        assert result["status"] == "error"
        assert "network down" in result["error"]

    def test_narrative_is_none_when_llm_not_configured(self):
        fake_info = {"longName": "Test Corp", "currentPrice": 100.0}
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(info=fake_info)):
            with patch.object(sr.llm_client, "is_configured", return_value=False):
                result = sr.financial_report_agent("TEST.NS")
        assert result["data"]["narrative"] is None

    def test_narrative_populated_when_llm_configured(self):
        fake_info = {"longName": "Test Corp", "currentPrice": 100.0}
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(info=fake_info)):
            with patch.object(sr.llm_client, "is_configured", return_value=True):
                with patch.object(sr.llm_client, "complete", return_value="Solid fundamentals.") as mock_complete:
                    result = sr.financial_report_agent("TEST.NS")
        assert result["data"]["narrative"] == "Solid fundamentals."
        mock_complete.assert_called_once()


class TestPnlCheckerAgent:
    def test_computes_growth_and_margin(self):
        periods = [pd.Timestamp("2025-03-31"), pd.Timestamp("2024-03-31")]
        financials = pd.DataFrame(
            {
                periods[0]: {"Total Revenue": 200.0, "Net Income": 20.0, "Gross Profit": 80.0, "Operating Income": 40.0},
                periods[1]: {"Total Revenue": 100.0, "Net Income": 10.0, "Gross Profit": 40.0, "Operating Income": 20.0},
            }
        )
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(financials=financials)):
            result = sr.pnl_checker_agent("TEST.NS")
        assert result["status"] == "ok"
        assert result["data"]["revenue_growth_pct"] == pytest.approx(100.0)
        assert result["data"]["net_margin_pct"] == pytest.approx(10.0)

    def test_no_data_on_empty_financials(self):
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(financials=pd.DataFrame())):
            result = sr.pnl_checker_agent("TEST.NS")
        assert result["status"] == "no_data"


class TestNewsValidationAgent:
    def test_counts_recent_and_tallies_tone(self):
        recent_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        stale_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat().replace("+00:00", "Z")
        fake_news = [
            {"content": {"title": "Company beats profit estimates", "summary": "", "pubDate": recent_iso,
                          "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "http://x/1"}}},
            {"content": {"title": "Old news about a lawsuit", "summary": "", "pubDate": stale_iso,
                          "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "http://x/2"}}},
        ]
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(news=fake_news)):
            result = sr.news_validation_agent("TEST.NS", lookback_days=30)
        assert result["status"] == "ok"
        assert result["data"]["total_articles"] == 2
        assert result["data"]["recent_articles"] == 1
        assert result["data"]["tone_counts"]["positive"] == 1
        assert result["data"]["tone_counts"]["negative"] == 1

    def test_no_data_on_empty_news(self):
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(news=[])):
            result = sr.news_validation_agent("TEST.NS")
        assert result["status"] == "no_data"

    def test_llm_sentiment_none_when_not_configured(self):
        fake_news = [{"content": {"title": "Company beats profit estimates", "summary": "", "pubDate": None,
                                   "provider": {}, "canonicalUrl": {}}}]
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(news=fake_news)):
            with patch.object(sr.llm_client, "is_configured", return_value=False):
                result = sr.news_validation_agent("TEST.NS")
        assert result["data"]["llm_sentiment"] is None

    def test_llm_sentiment_parses_label_and_rationale(self):
        fake_news = [{"content": {"title": "Company beats profit estimates", "summary": "", "pubDate": None,
                                   "provider": {}, "canonicalUrl": {}}}]
        llm_reply = "Label: bullish\nRationale: Strong earnings beat drove positive coverage."
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(news=fake_news)):
            with patch.object(sr.llm_client, "is_configured", return_value=True):
                with patch.object(sr.llm_client, "complete", return_value=llm_reply):
                    result = sr.news_validation_agent("TEST.NS")
        assert result["data"]["llm_sentiment"] == {
            "label": "bullish",
            "rationale": "Strong earnings beat drove positive coverage.",
        }

    def test_llm_sentiment_falls_back_to_neutral_on_unparseable_reply(self):
        fake_news = [{"content": {"title": "Company beats profit estimates", "summary": "", "pubDate": None,
                                   "provider": {}, "canonicalUrl": {}}}]
        with patch.object(sr.yf, "Ticker", return_value=SimpleNamespace(news=fake_news)):
            with patch.object(sr.llm_client, "is_configured", return_value=True):
                with patch.object(sr.llm_client, "complete", return_value="unstructured garbage"):
                    result = sr.news_validation_agent("TEST.NS")
        assert result["data"]["llm_sentiment"]["label"] == "neutral"


class TestAnalystVerdictAgent:
    def test_unavailable_when_llm_not_configured(self):
        with patch.object(sr.llm_client, "is_configured", return_value=False):
            result = sr.analyst_verdict_agent("TEST.NS", {}, {})
        assert result["status"] == "unavailable"

    def test_ok_with_narrative_when_configured(self):
        with patch.object(sr.llm_client, "is_configured", return_value=True):
            with patch.object(sr.llm_client, "complete", return_value="Summary: all good.") as mock_complete:
                result = sr.analyst_verdict_agent(
                    "TEST.NS",
                    {"rsi": {"status": "ok", "data": {"rsi": 50}}},
                    {"bullish_signals": 1, "bearish_signals": 0, "neutral_signals": 0},
                )
        assert result["status"] == "ok"
        assert result["data"]["narrative"] == "Summary: all good."
        mock_complete.assert_called_once()

    def test_error_when_llm_returns_nothing(self):
        with patch.object(sr.llm_client, "is_configured", return_value=True):
            with patch.object(sr.llm_client, "complete", return_value=None):
                result = sr.analyst_verdict_agent("TEST.NS", {}, {})
        assert result["status"] == "error"

    def test_never_raises_on_unexpected_exception(self):
        with patch.object(sr.llm_client, "is_configured", return_value=True):
            with patch.object(sr.llm_client, "complete", side_effect=RuntimeError("boom")):
                result = sr.analyst_verdict_agent("TEST.NS", {}, {})
        assert result["status"] == "error"
        assert "boom" in result["error"]


class TestLoadSymbolUniverse:
    def test_reads_cached_csv(self, tmp_path):
        path = tmp_path / "universe.csv"
        pd.DataFrame(
            {"symbol": ["AAA.NS", "BBB.NS"], "name": ["Alpha", "Beta"], "series": ["EQ", "EQ"]}
        ).to_csv(path, index=False)
        df = sr.load_symbol_universe(path)
        assert list(df["symbol"]) == ["AAA.NS", "BBB.NS"]

    def test_missing_file_returns_empty_frame(self, tmp_path):
        df = sr.load_symbol_universe(tmp_path / "missing.csv")
        assert df.empty
        assert list(df.columns) == ["symbol", "name", "series"]


class TestConsensus:
    def test_tallies_bullish_and_bearish_signals(self):
        agents = {
            "ema_vwap": {"data": {"trend_bias": "bullish", "vwap_position": "above_vwap"}},
            "rsi": {"data": {"classification": "oversold"}},
            "pattern_recognizer": {"data": {"passed": True}},
        }
        consensus = sr._consensus(agents)
        assert consensus == {"bullish_signals": 4, "bearish_signals": 0, "neutral_signals": 0}

    def test_handles_missing_agents_gracefully(self):
        assert sr._consensus({}) == {"bullish_signals": 0, "bearish_signals": 0, "neutral_signals": 0}


class _FakeIngestion:
    """Fake DataIngestion so run_stock_review's OHLCV fetch is network-free."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def fetch_ohlcv_with_quality(self, symbol, start_date=None, end_date=None, use_cache=True):
        quality = SimpleNamespace(to_dict=lambda: {"symbol": symbol, "source": "fake"})
        return SimpleNamespace(symbol=symbol, data=self._df, quality=quality)


class TestRunStockReview:
    def test_consolidates_all_seven_agents(self):
        df = _make_ohlcv(n=90, trend="up")
        fake_info = {"longName": "Test Corp", "currentPrice": 100.0}
        with patch.object(sr.yf, "Ticker") as mock_ticker:
            mock_ticker.return_value = SimpleNamespace(
                info=fake_info, financials=pd.DataFrame(), news=[]
            )
            with patch.object(sr.llm_client, "is_configured", return_value=False):
                report = sr.run_stock_review(
                    "TEST", months=6, ingestion=_FakeIngestion(df)
                )

        assert report["symbol"] == "TEST.NS"
        expected_agents = {
            "financial_report", "pnl_checker", "candlesticks",
            "pattern_recognizer", "news_validation", "ema_vwap", "rsi",
        }
        assert set(report["agents"]) == expected_agents
        for name, result in report["agents"].items():
            assert result["status"] in {"ok", "no_data"}, f"{name} unexpectedly errored: {result}"
        assert "consensus" in report
        assert report["analyst_verdict"]["status"] == "unavailable"

    def test_analyst_verdict_runs_after_other_agents_when_llm_configured(self):
        df = _make_ohlcv(n=90, trend="up")
        fake_info = {"longName": "Test Corp", "currentPrice": 100.0}
        with patch.object(sr.yf, "Ticker") as mock_ticker:
            mock_ticker.return_value = SimpleNamespace(
                info=fake_info, financials=pd.DataFrame(), news=[]
            )
            with patch.object(sr.llm_client, "is_configured", return_value=True):
                with patch.object(sr.llm_client, "complete", return_value="Summary: fine."):
                    report = sr.run_stock_review(
                        "TEST", months=6, ingestion=_FakeIngestion(df)
                    )

        assert report["analyst_verdict"]["status"] == "ok"
        assert report["analyst_verdict"]["data"]["narrative"] == "Summary: fine."
