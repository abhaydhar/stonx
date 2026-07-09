"""
Mockable web-research source interfaces (RES-01).

This module defines the *interface* the Research Agent uses to pull external
signals (news headlines, a light fundamentals snapshot) plus deterministic,
fully-offline implementations so the pipeline runs and unit tests assert
behaviour WITHOUT any network access.

Nothing here performs live HTTP. The only "real" provider (:class:`HttpWebSource`)
is a clearly-marked stub that raises :class:`NotImplementedError` and lazily
imports any network dependency, so importing this module never requires
``requests`` / scraping libraries.
"""

from __future__ import annotations

from typing import Dict, List, Protocol, runtime_checkable


def _normalize(symbol: str) -> str:
    """Best-effort symbol normalisation (local, dependency-free).

    Kept intentionally lightweight so this module has no heavy imports. Upper-
    cases and strips whitespace; leaves any exchange suffix (e.g. ``.NS``) as-is.
    """

    return str(symbol or "").strip().upper()


@runtime_checkable
class WebResearchSource(Protocol):
    """Contract the Research Agent needs from an external-signal provider."""

    def search_news(self, symbol: str, limit: int = 5) -> List[Dict]:
        """Return up to ``limit`` news items.

        Each item is a dict with keys: ``date`` (ISO ``YYYY-MM-DD``),
        ``headline``, ``source``, ``url``, ``snippet``.
        """
        ...

    def get_fundamentals_snapshot(self, symbol: str) -> Dict:
        """Return a light fundamentals snapshot dict.

        Suggested keys: ``as_of`` (ISO date), ``promoter_holding_pct``, ``notes``.
        May return ``{}`` when nothing is known.
        """
        ...


# ---------------------------------------------------------------------------
# Deterministic, offline default news fixtures.
# Keyed by normalised symbol. Unknown symbols return an empty list so callers
# can exercise the "no citations" red-flag path deterministically.
# ---------------------------------------------------------------------------
_DEFAULT_NEWS: Dict[str, List[Dict]] = {
    "TATAMOTORS.NS": [
        {
            "date": "2026-07-05",
            "headline": "Tata Motors surges as brokerage upgrades on record JLR profit",
            "source": "MoneyControl",
            "url": "https://example.com/tatamotors-upgrade",
            "snippet": "Strong quarterly growth and robust order book boost sentiment.",
        },
        {
            "date": "2026-07-02",
            "headline": "Tata Motors gains on strong EV sales rally",
            "source": "Economic Times",
            "url": "https://example.com/tatamotors-ev",
            "snippet": "Analysts stay bullish citing expansion and upbeat guidance.",
        },
    ],
    "INFY.NS": [
        {
            "date": "2026-07-04",
            "headline": "Infosys reports steady revenue; outlook unchanged",
            "source": "Business Standard",
            "url": "https://example.com/infy-results",
            "snippet": "Management keeps guidance intact for the fiscal year.",
        },
    ],
}


class StubWebSource:
    """Deterministic, offline :class:`WebResearchSource`.

    Returns a small canned news set keyed by symbol (empty for unknown symbols)
    and an empty fundamentals snapshot by default. Callers/tests may inject their
    own ``records`` / ``fundamentals`` dicts to script behaviour. No network.
    """

    name = "stub-web"

    def __init__(
        self,
        records: Dict[str, List[Dict]] | None = None,
        fundamentals: Dict[str, Dict] | None = None,
    ):
        # Default to a copy of the built-in fixtures unless overridden.
        self._records: Dict[str, List[Dict]] = (
            dict(_DEFAULT_NEWS) if records is None else dict(records)
        )
        self._fundamentals: Dict[str, Dict] = dict(fundamentals or {})

    def search_news(self, symbol: str, limit: int = 5) -> List[Dict]:
        items = self._records.get(_normalize(symbol), [])
        # Return shallow copies so callers cannot mutate the fixtures.
        return [dict(item) for item in items[: max(0, int(limit))]]

    def get_fundamentals_snapshot(self, symbol: str) -> Dict:
        return dict(self._fundamentals.get(_normalize(symbol), {}))


class NullWebSource:
    """A :class:`WebResearchSource` that knows nothing.

    Useful to exercise the no-citations red-flag path.
    """

    name = "null-web"

    def search_news(self, symbol: str, limit: int = 5) -> List[Dict]:
        return []

    def get_fundamentals_snapshot(self, symbol: str) -> Dict:
        return {}


class HttpWebSource:
    """Placeholder for a real HTTP-backed provider — intentionally NOT implemented.

    Live scraping / API calls are out of scope for the deterministic pipeline and
    the test environment (no network, no ``requests``). Any network dependency
    must be imported lazily inside the method bodies when this is eventually
    implemented, never at module import time.
    """

    name = "http-web"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "HttpWebSource is a stub. Live web research is not implemented; "
            "use StubWebSource (offline) or inject a real WebResearchSource."
        )

    def search_news(self, symbol: str, limit: int = 5) -> List[Dict]:  # pragma: no cover
        raise NotImplementedError

    def get_fundamentals_snapshot(self, symbol: str) -> Dict:  # pragma: no cover
        raise NotImplementedError
