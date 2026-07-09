"""
Trade journal and persistence layer (PRD Wave 3, DB-01..DB-03).

This module owns the SQLite-backed persistence for the scanner/agent workflow:

    * scanner candidates and rejected setups (candidate history)
    * per-agent reasoning (research / risk / execution / ...)
    * open positions (trade lifecycle) and closed trades (outcomes)

It uses the SQLAlchemy 2.0 declarative style (``DeclarativeBase`` + ``Mapped`` +
``mapped_column``) and exposes a single ``TradeJournal`` service class whose
methods are individually transactional. Query methods return plain dicts to keep
callers free of ``DetachedInstanceError`` concerns.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    """Naive local timestamp used for ``created_at`` / lifecycle defaults."""
    return datetime.now()


def _getattr(obj: Any, name: str, default: Any = None) -> Any:
    """getattr that also understands plain dicts (duck-typed inputs)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all journal tables."""


class Candidate(Base):
    """A scanner candidate or a rejected setup from a single scan run."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pattern: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rr_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position_shares: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    position_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    capital_at_risk_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_regime: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    rejection_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentDecision(Base):
    """Persisted reasoning/decision from an agent for a symbol in a run."""

    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    agent_name: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OpenPosition(Base):
    """An open trade being monitored (trade lifecycle)."""

    __tablename__ = "open_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[int] = mapped_column(Integer)
    capital_at_risk_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    current_stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    pattern: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ClosedTrade(Base):
    """A completed trade with realized outcome."""

    __tablename__ = "closed_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    shares: Mapped[int] = mapped_column(Integer)
    pnl: Mapped[float] = mapped_column(Float)
    pnl_percent: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    bars_held: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))
    exit_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    pattern: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


def _as_dict(obj: Base) -> Dict[str, Any]:
    """Convert an ORM row to a plain dict of its column values."""
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _make_engine(db_url: str) -> Engine:
    """Create a SQLite engine, keeping in-memory DBs alive via StaticPool."""
    url = db_url.strip()
    is_memory = url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url
    if is_memory:
        return create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, echo=False)


class TradeJournal:
    """SQLite-backed persistence service for candidates, decisions, and trades."""

    def __init__(
        self,
        db_url: Optional[str] = None,
        engine: Optional[Engine] = None,
    ):
        if engine is not None:
            self.engine = engine
        else:
            if db_url is None:
                from config import get_config

                db_url = get_config().DATABASE_URL
            self.engine = _make_engine(db_url)

        Base.metadata.create_all(self.engine)
        # expire_on_commit=False so returned/expunged objects keep their values.
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        """Transactional session helper: commit on success, rollback on error."""
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Candidate history (DB-03)
    # ------------------------------------------------------------------

    def record_scan(self, output: Any, run_id: Optional[str] = None) -> str:
        """Persist all candidates and rejected setups from a ScannerOutput.

        ``output`` may be a real ``ScannerOutput`` or any duck-typed object /
        dict exposing ``candidates`` and ``rejected`` iterables. Returns the
        run_id under which the rows were stored.
        """
        if run_id is None:
            run_id = f"{_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        regime = _getattr(output, "market_regime")
        top_regime = _getattr(regime, "regime", regime) if regime is not None else None
        if not isinstance(top_regime, (str, type(None))):
            top_regime = str(top_regime)

        candidates = _getattr(output, "candidates", []) or []
        rejected = _getattr(output, "rejected", []) or []

        with self._session() as session:
            for cand in candidates:
                session.add(
                    Candidate(
                        run_id=run_id,
                        symbol=_getattr(cand, "symbol"),
                        sector=_getattr(cand, "sector"),
                        pattern=_getattr(cand, "pattern"),
                        confidence=_getattr(cand, "confidence"),
                        entry=_getattr(cand, "entry"),
                        stop=_getattr(cand, "stop"),
                        target=_getattr(cand, "target"),
                        rr_ratio=_getattr(cand, "rr_ratio"),
                        position_shares=_getattr(cand, "position_shares"),
                        position_inr=_getattr(cand, "position_inr"),
                        capital_at_risk_inr=_getattr(cand, "capital_at_risk_inr"),
                        market_regime=_getattr(cand, "market_regime", top_regime),
                        status="candidate",
                    )
                )
            for rej in rejected:
                session.add(
                    Candidate(
                        run_id=run_id,
                        symbol=_getattr(rej, "symbol"),
                        sector=_getattr(rej, "sector"),
                        pattern=_getattr(rej, "pattern"),
                        rr_ratio=_getattr(rej, "rr_ratio"),
                        market_regime=top_regime,
                        status="rejected",
                        rejection_stage=_getattr(rej, "stage"),
                        rejection_reason=_getattr(rej, "reason"),
                    )
                )
        return run_id

    def get_candidates(
        self,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return candidate/rejected rows, optionally filtered by run and status."""
        with self._session() as session:
            stmt = select(Candidate)
            if run_id is not None:
                stmt = stmt.where(Candidate.run_id == run_id)
            if status is not None:
                stmt = stmt.where(Candidate.status == status)
            stmt = stmt.order_by(Candidate.id)
            return [_as_dict(row) for row in session.scalars(stmt).all()]

    # ------------------------------------------------------------------
    # Agent reasoning
    # ------------------------------------------------------------------

    def record_agent_decision(
        self,
        run_id: Optional[str],
        symbol: str,
        agent_name: str,
        decision: str,
        reasoning: str = "",
        confidence: Optional[float] = None,
        payload: Optional[Any] = None,
    ) -> int:
        """Persist an agent decision (with full contract dict as JSON). Returns id."""
        payload_json = json.dumps(payload) if payload is not None else None
        with self._session() as session:
            row = AgentDecision(
                run_id=run_id,
                symbol=symbol,
                agent_name=agent_name,
                decision=decision,
                reasoning=reasoning,
                confidence=confidence,
                payload_json=payload_json,
            )
            session.add(row)
            session.flush()
            return row.id

    def get_agent_decisions(
        self,
        run_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return agent decisions, optionally filtered by run and/or symbol.

        Each dict includes the raw ``payload_json`` plus a decoded ``payload``.
        """
        with self._session() as session:
            stmt = select(AgentDecision)
            if run_id is not None:
                stmt = stmt.where(AgentDecision.run_id == run_id)
            if symbol is not None:
                stmt = stmt.where(AgentDecision.symbol == symbol)
            stmt = stmt.order_by(AgentDecision.id)
            rows = session.scalars(stmt).all()

        results: List[Dict[str, Any]] = []
        for row in rows:
            record = _as_dict(row)
            record["payload"] = (
                json.loads(row.payload_json) if row.payload_json is not None else None
            )
            results.append(record)
        return results

    # ------------------------------------------------------------------
    # Trade lifecycle (DB-02)
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        shares: int,
        sector: Optional[str] = "Unknown",
        capital_at_risk_inr: Optional[float] = None,
        entry_date: Optional[datetime] = None,
        pattern: Optional[str] = None,
        run_id: Optional[str] = None,
        notes: str = "",
        current_stop: Optional[float] = None,
    ) -> int:
        """Insert a new open position. Returns the position id."""
        if capital_at_risk_inr is None:
            capital_at_risk_inr = abs(float(entry_price) - float(stop_price)) * shares
        with self._session() as session:
            row = OpenPosition(
                symbol=symbol,
                sector=sector,
                entry_price=float(entry_price),
                stop_price=float(stop_price),
                target_price=float(target_price),
                shares=int(shares),
                capital_at_risk_inr=capital_at_risk_inr,
                entry_date=entry_date or _now(),
                current_stop=current_stop if current_stop is not None else float(stop_price),
                status="open",
                pattern=pattern,
                run_id=run_id,
                notes=notes,
            )
            session.add(row)
            session.flush()
            return row.id

    def update_position(self, position_id: int, **fields: Any) -> Dict[str, Any]:
        """Update whitelisted fields on an open position (e.g. current_stop).

        Returns the updated row as a dict.
        """
        allowed = {
            "stop_price",
            "target_price",
            "shares",
            "capital_at_risk_inr",
            "current_stop",
            "status",
            "pattern",
            "notes",
            "sector",
        }
        with self._session() as session:
            row = session.get(OpenPosition, position_id)
            if row is None:
                raise ValueError(f"No open position with id={position_id}")
            for key, value in fields.items():
                if key not in allowed:
                    raise ValueError(f"Cannot update field '{key}' on open position")
                setattr(row, key, value)
            session.flush()
            return _as_dict(row)

    def close_trade(
        self,
        position_id: int,
        exit_price: float,
        exit_date: Optional[datetime] = None,
        exit_reason: str = "",
        outcome: Optional[str] = None,
        bars_held: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Close an open position: compute pnl, move it to closed_trades atomically.

        Returns the created closed trade as a dict.
        """
        with self._session() as session:
            pos = session.get(OpenPosition, position_id)
            if pos is None:
                raise ValueError(f"No open position with id={position_id}")

            entry_price = float(pos.entry_price)
            shares = int(pos.shares)
            exit_price = float(exit_price)
            pnl = (exit_price - entry_price) * shares
            pnl_percent = (
                (exit_price - entry_price) / entry_price * 100.0 if entry_price else 0.0
            )

            if outcome is None:
                if pnl > 0:
                    outcome = "win"
                elif pnl < 0:
                    outcome = "loss"
                else:
                    outcome = "breakeven"

            closed = ClosedTrade(
                symbol=pos.symbol,
                sector=pos.sector,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=pos.stop_price,
                target_price=pos.target_price,
                shares=shares,
                pnl=pnl,
                pnl_percent=pnl_percent,
                entry_date=pos.entry_date,
                exit_date=exit_date or _now(),
                bars_held=bars_held,
                outcome=outcome,
                exit_reason=exit_reason,
                pattern=pos.pattern,
                run_id=pos.run_id,
            )
            session.add(closed)
            session.delete(pos)
            session.flush()
            result = _as_dict(closed)
        return result

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return all open positions as dicts."""
        with self._session() as session:
            stmt = select(OpenPosition).order_by(OpenPosition.id)
            return [_as_dict(row) for row in session.scalars(stmt).all()]

    def get_closed_trades(self) -> List[Dict[str, Any]]:
        """Return all closed trades as dicts."""
        with self._session() as session:
            stmt = select(ClosedTrade).order_by(ClosedTrade.id)
            return [_as_dict(row) for row in session.scalars(stmt).all()]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Aggregate performance across closed trades (guards div-by-zero)."""
        with self._session() as session:
            closed = session.scalars(select(ClosedTrade)).all()
            open_count = len(session.scalars(select(OpenPosition)).all())

        total_closed = len(closed)
        wins = sum(1 for t in closed if t.pnl > 0)
        losses = sum(1 for t in closed if t.pnl < 0)
        total_pnl = sum(t.pnl for t in closed)
        win_rate = (wins / total_closed) if total_closed else 0.0
        avg_pnl_percent = (
            sum(t.pnl_percent for t in closed) / total_closed if total_closed else 0.0
        )

        return {
            "total_closed": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl_percent": avg_pnl_percent,
            "open_count": open_count,
        }
