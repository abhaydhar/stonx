"""
Unit tests for modules/ingest.py.

All network calls are mocked/monkeypatched so the suite runs offline, per
project convention (see README "Testing").
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.ingest import DataIngestion, NSEArchiveUniverseProvider, UniverseMember


# ---------------------------------------------------------------------------
# NSEArchiveUniverseProvider
# ---------------------------------------------------------------------------

_FAKE_NIFTY_CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Reliance Industries Ltd.,Energy,RELIANCE,EQ,INE002A01018\n"
    "Tata Consultancy Services Ltd.,IT,TCS,EQ,INE467B01029\n"
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_provider_parses_nse_index_csv(monkeypatch):
    def fake_get(self, url, headers=None, timeout=None):
        if "nseindia.com" == url.rstrip("/").split("//")[-1]:
            return _FakeResponse("")
        return _FakeResponse(_FAKE_NIFTY_CSV)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    provider = NSEArchiveUniverseProvider()
    members = provider.fetch("nifty50")

    assert members == [
        UniverseMember(symbol="RELIANCE.NS", name="Reliance Industries Ltd.", sector="Energy"),
        UniverseMember(symbol="TCS.NS", name="Tata Consultancy Services Ltd.", sector="IT"),
    ]


def test_provider_rejects_unknown_index():
    provider = NSEArchiveUniverseProvider()
    with pytest.raises(ValueError):
        provider.fetch("not-a-real-index")


def test_provider_survives_warmup_failure(monkeypatch):
    def fake_get(self, url, headers=None, timeout=None):
        if "nseindia.com" in url and "archives" not in url:
            raise requests.RequestException("blocked")
        return _FakeResponse(_FAKE_NIFTY_CSV)

    monkeypatch.setattr(requests.Session, "get", fake_get)

    provider = NSEArchiveUniverseProvider()
    members = provider.fetch("nifty50")
    assert len(members) == 2


# ---------------------------------------------------------------------------
# DataIngestion live-universe integration
# ---------------------------------------------------------------------------

class _CountingProvider:
    source_name = "fake"

    def __init__(self, members):
        self.members = members
        self.calls = 0

    def fetch(self, index):
        self.calls += 1
        return self.members


def _ingestion(tmp_path, universe_provider=None, universe_index=None, ttl=24.0):
    universe_path = tmp_path / "nse_universe.csv"
    universe_path.write_text("symbol,name,sector\nINFY.NS,Infosys,IT\n", encoding="utf-8")
    return DataIngestion(
        cache_dir=str(tmp_path / "cache"),
        universe_path=str(universe_path),
        universe_provider=universe_provider,
        universe_index=universe_index,
        universe_cache_ttl_hours=ttl,
    )


def test_get_universe_uses_static_csv_when_no_live_index(tmp_path):
    ingestion = _ingestion(tmp_path)
    universe = ingestion.get_universe()
    assert [m.symbol for m in universe] == ["INFY.NS"]


def test_get_universe_prefers_live_provider_and_caches(tmp_path):
    fake_members = [UniverseMember(symbol="RELIANCE.NS", name="Reliance", sector="Energy")]
    provider = _CountingProvider(fake_members)
    ingestion = _ingestion(tmp_path, universe_provider=provider, universe_index="nifty50")

    universe = ingestion.get_universe()
    assert [m.symbol for m in universe] == ["RELIANCE.NS"]
    assert provider.calls == 1

    cache_path = tmp_path / "live_nifty50.csv"
    assert cache_path.exists()

    # A fresh DataIngestion within the TTL should read the cache, not the network.
    ingestion2 = _ingestion(tmp_path, universe_provider=provider, universe_index="nifty50")
    ingestion2.get_universe()
    assert provider.calls == 1


def test_get_universe_falls_back_to_static_csv_on_fetch_failure(tmp_path):
    class _FailingProvider:
        source_name = "failing"

        def fetch(self, index):
            raise RuntimeError("NSE is down")

    ingestion = _ingestion(tmp_path, universe_provider=_FailingProvider(), universe_index="nifty50")
    universe = ingestion.get_universe()
    assert [m.symbol for m in universe] == ["INFY.NS"]


def test_get_universe_uses_stale_cache_on_fetch_failure(tmp_path):
    fake_members = [UniverseMember(symbol="RELIANCE.NS", name="Reliance", sector="Energy")]
    good_provider = _CountingProvider(fake_members)
    ingestion = _ingestion(tmp_path, universe_provider=good_provider, universe_index="nifty50")
    ingestion.get_universe()  # populates the live cache file

    class _FailingProvider:
        source_name = "failing"

        def fetch(self, index):
            raise RuntimeError("NSE is down")

    ingestion2 = _ingestion(tmp_path, universe_provider=_FailingProvider(), universe_index="nifty50", ttl=0.0)
    universe = ingestion2.get_universe()
    assert [m.symbol for m in universe] == ["RELIANCE.NS"]
