"""
Alert tools (PRD Wave 6, ALERT-01..03).

Two small, dependency-light building blocks:

* :class:`AlertFormatter` renders human-readable text for a daily setup digest
  and for individual trade / stop-breach events. It accepts either dicts or
  duck-typed objects (e.g. ``ExecutionEvent``) via ``getattr``/``get``.

* :class:`AlertSender` delivers those messages. It defaults to **dry-run**
  mode (ALERT-02): in dry-run it never imports or touches ``apprise`` and
  performs no network I/O -- it just returns the formatted message. Only when
  ``dry_run=False`` *and* at least one apprise URL is configured does it lazily
  ``import apprise`` and attempt a real send (wrapped so it never raises).

Nothing here is imported at module load beyond the standard library, so the
module is safe to import in a bare environment.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Field access helpers (dict OR object)
# ---------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict (``.get``) or an object (``getattr``)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _fmt(value: Any) -> str:
    """Format a number to 2dp; pass strings through; ``None`` -> ``'-'``."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


# ---------------------------------------------------------------------------
# Formatter (ALERT-01)
# ---------------------------------------------------------------------------


class AlertFormatter:
    """Renders alert message bodies. Methods are static so they can be called
    on the class or an instance interchangeably."""

    @staticmethod
    def daily_summary(
        candidates: List[Any],
        market_regime: str = "",
        run_id: str = "",
    ) -> str:
        """Readable digest of the top scanner setups for the day."""
        candidates = list(candidates or [])
        header = "StockScanner Daily Setups"
        meta = []
        if run_id:
            meta.append(f"run {run_id}")
        if market_regime:
            meta.append(f"regime: {market_regime}")
        if meta:
            header += " | " + " | ".join(meta)

        lines: List[str] = [header, f"Top setups: {len(candidates)}", "-" * 40]

        if not candidates:
            lines.append("No candidates today.")
            return "\n".join(lines)

        for idx, cand in enumerate(candidates, start=1):
            symbol = _get(cand, "symbol", "?")
            pattern = _get(cand, "pattern", "") or ""
            entry = _get(cand, "entry", _get(cand, "entry_price"))
            stop = _get(cand, "stop", _get(cand, "stop_price"))
            target = _get(cand, "target", _get(cand, "target_price"))
            rr = _get(cand, "rr_ratio")
            lines.append(
                f"{idx}. {symbol} [{pattern}] "
                f"entry={_fmt(entry)} stop={_fmt(stop)} "
                f"target={_fmt(target)} R:R={_fmt(rr)}"
            )
        return "\n".join(lines)

    @staticmethod
    def stop_breach(event: Any) -> str:
        """Template for a STOP_BREACHED execution event."""
        symbol = _get(event, "symbol", "?")
        entry = _get(event, "entry_price")
        exit_price = _get(event, "exit_price")
        pnl = _get(event, "pnl")
        pnl_percent = _get(event, "pnl_percent")
        return (
            f"STOP BREACHED: {symbol}\n"
            f"Entry: {_fmt(entry)}  Stop/Exit: {_fmt(exit_price)}\n"
            f"P&L: {_fmt(pnl)} ({_fmt(pnl_percent)}%)"
        )

    @staticmethod
    def trade_event(event: Any) -> str:
        """Generic template for any execution event (open/target/trailing)."""
        event_type = _get(event, "event_type", "EVENT")
        symbol = _get(event, "symbol", "?")
        entry = _get(event, "entry_price")
        exit_price = _get(event, "exit_price")
        target = _get(event, "target_price")
        pnl = _get(event, "pnl")
        pnl_percent = _get(event, "pnl_percent")

        lines = [f"[{event_type}] {symbol}", f"Entry: {_fmt(entry)}"]
        if exit_price is not None:
            lines.append(f"Exit: {_fmt(exit_price)}")
        if target is not None:
            lines.append(f"Target: {_fmt(target)}")
        lines.append(f"P&L: {_fmt(pnl)} ({_fmt(pnl_percent)}%)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sender (ALERT-02)
# ---------------------------------------------------------------------------


class AlertSender:
    """Delivers alert messages; dry-run by default (no network, no apprise).

    Parameters
    ----------
    dry_run:
        When ``True`` (default) messages are formatted and returned but never
        actually sent, and ``apprise`` is never imported.
    apprise_urls:
        Optional list of apprise service URLs used only for live sends.
    """

    def __init__(
        self,
        dry_run: bool = True,
        apprise_urls: Optional[List[str]] = None,
    ):
        self.dry_run = dry_run
        self.apprise_urls: List[str] = list(apprise_urls) if apprise_urls else []
        self.formatter = AlertFormatter()

    def send(self, title: str, body: str) -> Dict[str, Any]:
        """Send (or dry-run) a message. Never raises.

        Returns a result dict: ``{sent, dry_run, channels, message}``.
        """
        message = f"{title}\n{body}" if title else (body or "")

        # Dry-run OR nothing to send to: return without importing apprise.
        if self.dry_run or not self.apprise_urls:
            return {
                "sent": False,
                "dry_run": self.dry_run,
                "channels": 0,
                "message": message,
            }

        # Live path (ALERT-02): only reachable with dry_run=False AND urls.
        try:
            import apprise  # lazy import — never at module load

            apobj = apprise.Apprise()
            channels = 0
            for url in self.apprise_urls:
                if apobj.add(url):
                    channels += 1
            sent = bool(apobj.notify(title=title or "", body=body or ""))
            return {
                "sent": sent,
                "dry_run": False,
                "channels": channels,
                "message": message,
            }
        except Exception as exc:  # pragma: no cover - defensive; never raise
            return {
                "sent": False,
                "dry_run": False,
                "channels": 0,
                "message": message,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Convenience wrappers (ALERT-01 formatting + ALERT-02 delivery)
    # ------------------------------------------------------------------

    def send_daily_summary(
        self,
        candidates: List[Any],
        market_regime: str = "",
        run_id: str = "",
    ) -> Dict[str, Any]:
        """Format and send the daily setup digest."""
        body = self.formatter.daily_summary(
            candidates, market_regime=market_regime, run_id=run_id
        )
        title = "StockScanner Daily Setups"
        if run_id:
            title += f" ({run_id})"
        return self.send(title, body)

    def send_execution_event(self, event: Any) -> Dict[str, Any]:
        """Format and send a single execution event."""
        event_type = _get(event, "event_type", "EVENT")
        symbol = _get(event, "symbol", "?")
        if event_type == "STOP_BREACHED":
            body = self.formatter.stop_breach(event)
        else:
            body = self.formatter.trade_event(event)
        title = f"{event_type}: {symbol}"
        return self.send(title, body)
