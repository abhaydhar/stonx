"""
Execution Agent (PRD Wave 6, EXEC-01..03).

A deterministic, network-free monitor for open trades. It reads open positions
from a :class:`~modules.journal.TradeJournal`, fetches a current price for each
symbol from a pluggable *price provider*, and decides one of four outcomes per
position:

    * ``STOP_BREACHED``       -> close the trade at the (effective) stop.
    * ``TARGET_HIT``          -> close the trade at the target.
    * ``TRAILING_STOP_MOVED`` -> move the stop to breakeven after +1R (EXEC-03).
    * ``OPEN``                -> nothing to do; still monitoring.

Every decision is emitted as an :class:`ExecutionEvent` matching the PRD
"Execution Event" contract. Closing / trailing actions are persisted through
the journal so the trade lifecycle stays authoritative.

This module deliberately imports nothing heavy (no CrewAI / LangChain / apprise)
so it can be imported and unit-tested in a bare environment. ``TradeJournal`` is
only referenced for typing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:  # pragma: no cover - typing only
    from modules.journal import TradeJournal


# ---------------------------------------------------------------------------
# Event types (PRD contract enum)
# ---------------------------------------------------------------------------

STOP_BREACHED = "STOP_BREACHED"
TARGET_HIT = "TARGET_HIT"
TRAILING_STOP_MOVED = "TRAILING_STOP_MOVED"
OPEN = "OPEN"

#: Event types that warrant an outbound alert (if an alert_sender is wired).
ALERT_EVENTS = frozenset({STOP_BREACHED, TARGET_HIT})


# ---------------------------------------------------------------------------
# Execution Event (PRD "Execution Event" contract)
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEvent:
    """Result of evaluating a single open position.

    ``to_dict()`` returns exactly the PRD contract fields. The extra attributes
    (``current_price``, ``position_id``, ``reason``) are retained on the object
    for callers/logging but intentionally kept out of the canonical dict.
    """

    symbol: str
    event_type: str
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    alert_sent: bool = False
    journal_updated: bool = False

    # --- non-contract extras -------------------------------------------------
    current_price: Optional[float] = None
    position_id: Optional[int] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the canonical PRD Execution Event dict (8 fields, in order)."""
        return {
            "symbol": self.symbol,
            "event_type": self.event_type,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "alert_sent": self.alert_sent,
            "journal_updated": self.journal_updated,
        }


# ---------------------------------------------------------------------------
# Price provider abstraction (no network)
# ---------------------------------------------------------------------------

PriceProvider = Union[Callable[[str], Optional[float]], Any]


class DictPriceProvider:
    """A trivial in-memory price provider for tests / mocking.

    Works both as an object with ``.get_price(symbol)`` and as a plain callable
    (``provider(symbol)``) so it satisfies either half of the price-provider
    contract accepted by :class:`ExecutionAgent`.
    """

    def __init__(self, prices: Optional[Dict[str, float]] = None):
        self.prices: Dict[str, float] = dict(prices or {})

    def get_price(self, symbol: str) -> Optional[float]:
        value = self.prices.get(symbol)
        return None if value is None else float(value)

    # Also usable as a bare callable.
    def __call__(self, symbol: str) -> Optional[float]:
        return self.get_price(symbol)


def resolve_price(provider: PriceProvider, symbol: str) -> Optional[float]:
    """Fetch a price from a provider that is either ``.get_price`` or callable.

    Returns ``None`` when no price is available (unknown symbol / no provider).
    """
    if provider is None:
        return None
    getter = getattr(provider, "get_price", None)
    if callable(getter):
        value = getter(symbol)
    elif callable(provider):
        value = provider(symbol)
    else:
        return None
    return None if value is None else float(value)


def _unrealized(entry: float, price: float, shares: int) -> tuple[float, float]:
    """Mark-to-market P&L (absolute, percent) for an open position."""
    pnl = (price - entry) * shares
    pnl_percent = ((price - entry) / entry * 100.0) if entry else 0.0
    return pnl, pnl_percent


# ---------------------------------------------------------------------------
# Execution Agent
# ---------------------------------------------------------------------------


class ExecutionAgent:
    """Monitors open positions and emits :class:`ExecutionEvent` decisions.

    Parameters
    ----------
    journal:
        A :class:`~modules.journal.TradeJournal` (or duck-typed equivalent).
    price_provider:
        Either a callable ``symbol -> float | None`` or an object exposing
        ``.get_price(symbol) -> float | None``. No network access is performed
        by this class.
    alert_sender:
        Optional object with ``.send_execution_event(event)``. When supplied,
        stop-breach / target-hit events are dispatched through it and the
        event's ``alert_sent`` flag is set. A dry-run sender performs no live
        network send.
    one_r_trail:
        When ``True`` (default), advance the stop to breakeven once price has
        moved at least +1R (1R = entry - original stop) in favour of the trade.
    """

    def __init__(
        self,
        journal: "TradeJournal",
        price_provider: PriceProvider,
        alert_sender: Optional[Any] = None,
        one_r_trail: bool = True,
    ):
        self.journal = journal
        self.price_provider = price_provider
        self.alert_sender = alert_sender
        self.one_r_trail = one_r_trail

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_positions(self) -> List[ExecutionEvent]:
        """Evaluate every open position and return the resulting events.

        Positions with no available current price are skipped (no event).
        """
        events: List[ExecutionEvent] = []
        for pos in self.journal.get_open_positions():
            event = self._evaluate_position(pos)
            if event is not None:
                events.append(event)
        return events

    def run_once(self) -> Dict[str, Any]:
        """Convenience: run one monitoring pass and include a journal summary."""
        events = self.check_positions()
        return {"events": events, "summary": self.journal.summary()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _price(self, symbol: str) -> Optional[float]:
        return resolve_price(self.price_provider, symbol)

    def _evaluate_position(self, pos: Dict[str, Any]) -> Optional[ExecutionEvent]:
        symbol = pos.get("symbol")
        price = self._price(symbol)
        if price is None:
            return None

        entry = float(pos["entry_price"])
        stop_price = float(pos["stop_price"])
        target = float(pos["target_price"])
        shares = int(pos["shares"])
        position_id = pos.get("id")

        current_stop = pos.get("current_stop")
        effective_stop = (
            float(current_stop) if current_stop is not None else stop_price
        )

        # EXEC-02: stop breach (evaluated against the effective/current stop).
        if price <= effective_stop:
            result = self.journal.close_trade(
                position_id,
                exit_price=effective_stop,
                exit_reason="stop",
                outcome="loss",
            )
            event = ExecutionEvent(
                symbol=symbol,
                event_type=STOP_BREACHED,
                entry_price=entry,
                exit_price=effective_stop,
                pnl=result.get("pnl"),
                pnl_percent=result.get("pnl_percent"),
                journal_updated=True,
                current_price=price,
                position_id=position_id,
                reason="stop",
            )

        # EXEC-02: target hit.
        elif price >= target:
            result = self.journal.close_trade(
                position_id,
                exit_price=target,
                exit_reason="target",
                outcome="win",
            )
            event = ExecutionEvent(
                symbol=symbol,
                event_type=TARGET_HIT,
                entry_price=entry,
                exit_price=target,
                pnl=result.get("pnl"),
                pnl_percent=result.get("pnl_percent"),
                journal_updated=True,
                current_price=price,
                position_id=position_id,
                reason="target",
            )

        else:
            one_r = entry - stop_price
            advanced_1r = one_r > 0 and price >= entry + one_r

            # EXEC-03: trail stop to breakeven once +1R is reached.
            if self.one_r_trail and advanced_1r and effective_stop < entry:
                notes = (
                    f"Trailing stop moved to breakeven ({entry:.2f}) "
                    f"after +1R reached @ {price:.2f}"
                )
                self.journal.update_position(
                    position_id, current_stop=entry, notes=notes
                )
                unreal_pnl, unreal_pct = _unrealized(entry, price, shares)
                event = ExecutionEvent(
                    symbol=symbol,
                    event_type=TRAILING_STOP_MOVED,
                    entry_price=entry,
                    exit_price=None,
                    pnl=unreal_pnl,
                    pnl_percent=unreal_pct,
                    journal_updated=True,
                    current_price=price,
                    position_id=position_id,
                    reason="trail_to_breakeven",
                )
            else:
                unreal_pnl, unreal_pct = _unrealized(entry, price, shares)
                event = ExecutionEvent(
                    symbol=symbol,
                    event_type=OPEN,
                    entry_price=entry,
                    exit_price=None,
                    pnl=unreal_pnl,
                    pnl_percent=unreal_pct,
                    journal_updated=False,
                    current_price=price,
                    position_id=position_id,
                    reason="no_action",
                )

        self._maybe_alert(event)
        return event

    def _maybe_alert(self, event: ExecutionEvent) -> None:
        """Dispatch alert-worthy events through the sender (never raises).

        ``alert_sent`` reflects that an alert was *dispatched* to the sender; a
        dry-run sender still counts as dispatched but performs no live send.
        """
        if self.alert_sender is None or event.event_type not in ALERT_EVENTS:
            return
        try:
            self.alert_sender.send_execution_event(event)
        except Exception:  # pragma: no cover - defensive; alerts must not break monitoring
            pass
        event.alert_sent = True
