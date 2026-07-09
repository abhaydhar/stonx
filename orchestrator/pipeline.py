"""
Deterministic multi-agent pipeline: scanner -> research -> risk (ORCH-01),
persisting scanner candidates and agent reasoning to the trade journal so the
dashboard can query reasoning by run/symbol (ORCH-02).

Design:
  * LLM-free by default. Research/Risk agents fall back to DeterministicLLM,
    so the whole flow runs offline and is unit-testable without crewai/langchain
    or an API key.
  * Every collaborator is injectable (scanner, research_agent, risk_agent,
    journal, price/benchmark maps) so tests use fakes and temp SQLite.
  * The legacy CrewAI ``orchestrator/crew.py`` is left intact for the LLM-driven
    path; this module is the deterministic spine the app and tests rely on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


APPROVED_STATUSES = {"APPROVED", "CONDITIONAL"}


@dataclass
class PipelineDecision:
    """Combined scanner + research + risk view for one symbol."""

    symbol: str
    sector: str
    pattern: str
    entry: float
    stop: float
    target: float
    rr_ratio: float
    position_shares: int
    approval_status: str
    position_size_multiplier: float
    adjusted_shares: int
    sentiment_score: Optional[float]
    research_confidence: Optional[float]
    risk_confidence: Optional[float]
    concerns: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    research_summary: str = ""
    decision_reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "pattern": self.pattern,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "rr_ratio": self.rr_ratio,
            "position_shares": self.position_shares,
            "approval_status": self.approval_status,
            "position_size_multiplier": self.position_size_multiplier,
            "adjusted_shares": self.adjusted_shares,
            "sentiment_score": self.sentiment_score,
            "research_confidence": self.research_confidence,
            "risk_confidence": self.risk_confidence,
            "concerns": list(self.concerns),
            "red_flags": list(self.red_flags),
            "research_summary": self.research_summary,
            "decision_reasoning": self.decision_reasoning,
        }


@dataclass
class PipelineResult:
    run_id: str
    scanner_output: Any
    decisions: List[PipelineDecision] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def approved(self) -> List[PipelineDecision]:
        return [d for d in self.decisions if d.approval_status in APPROVED_STATUSES]

    @property
    def rejected(self) -> List[PipelineDecision]:
        return [d for d in self.decisions if d.approval_status not in APPROVED_STATUSES]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "counts": self.counts,
            "market_regime": _regime_value(self.scanner_output),
            "funnel_counts": getattr(self.scanner_output, "funnel_counts", {}),
            "decisions": [d.to_dict() for d in self.decisions],
            "approved": [d.to_dict() for d in self.approved],
        }


def _regime_value(scanner_output: Any) -> str:
    regime = getattr(scanner_output, "market_regime", None)
    return getattr(regime, "regime", "unknown") if regime is not None else "unknown"


class ScanResearchRiskPipeline:
    """Runs scanner -> research -> risk and persists reasoning."""

    def __init__(
        self,
        scanner: Optional[Any] = None,
        research_agent: Optional[Any] = None,
        risk_agent: Optional[Any] = None,
        journal: Optional[Any] = None,
    ):
        self._scanner = scanner
        self._research_agent = research_agent
        self._risk_agent = risk_agent
        self._journal = journal

    # -- lazy builders so imports stay cheap and offline-friendly ----------

    def _scanner_obj(self):
        if self._scanner is None:
            from modules.scanner import DeterministicScanner

            self._scanner = DeterministicScanner()
        return self._scanner

    def _research(self):
        if self._research_agent is None:
            from agents.research_agent import ResearchAgent

            self._research_agent = ResearchAgent()
        return self._research_agent

    def _risk(self):
        if self._risk_agent is None:
            from agents.risk_agent import RiskAgent

            self._risk_agent = RiskAgent()
        return self._risk_agent

    def _journal_obj(self):
        if self._journal is None:
            from modules.journal import TradeJournal

            self._journal = TradeJournal()
        return self._journal

    # ---------------------------------------------------------------------

    def run(
        self,
        symbols: Optional[List[str]] = None,
        limit: Optional[int] = None,
        market_regime: Optional[str] = None,
        portfolio: Optional[Any] = None,
        use_cache: bool = True,
        persist: bool = True,
        run_id: Optional[str] = None,
        price_map: Optional[Dict[str, Any]] = None,
        benchmark: Optional[Any] = None,
        fundamentals_map: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        scanner_output = self._scanner_obj().run(
            symbols=symbols,
            limit=limit,
            market_regime=market_regime,
            portfolio=portfolio,
            use_cache=use_cache,
        )

        journal = self._journal_obj() if persist else None
        if journal is not None:
            run_id = journal.record_scan(scanner_output, run_id=run_id)
        elif run_id is None:
            from datetime import datetime

            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        research_agent = self._research()
        risk_agent = self._risk()
        price_map = price_map or {}
        fundamentals_map = fundamentals_map or {}

        decisions: List[PipelineDecision] = []
        candidates = list(getattr(scanner_output, "candidates", []))
        for candidate in candidates:
            symbol = getattr(candidate, "symbol", None)
            if not symbol:
                continue

            research = research_agent.research(
                candidate,
                fundamentals=fundamentals_map.get(symbol),
            )
            research_dict = research.to_dict()

            risk = risk_agent.challenge(
                candidate,
                research=research_dict,
                price_data=price_map.get(symbol),
                benchmark=benchmark,
                portfolio=portfolio,
            )
            risk_dict = risk.to_dict()

            if journal is not None:
                journal.record_agent_decision(
                    run_id=run_id,
                    symbol=symbol,
                    agent_name="research",
                    decision=_sentiment_label(research_dict.get("sentiment_score")),
                    reasoning=research_dict.get("research_summary", ""),
                    confidence=research_dict.get("confidence_score"),
                    payload=research_dict,
                )
                journal.record_agent_decision(
                    run_id=run_id,
                    symbol=symbol,
                    agent_name="risk",
                    decision=risk_dict.get("approval_status", "UNKNOWN"),
                    reasoning=risk_dict.get("decision_reasoning", ""),
                    confidence=risk_dict.get("confidence_after_challenge"),
                    payload=risk_dict,
                )

            decisions.append(
                _build_decision(candidate, research_dict, risk_dict)
            )

        counts = {
            "scanned_candidates": len(candidates),
            "approved": sum(1 for d in decisions if d.approval_status in APPROVED_STATUSES),
            "rejected_by_risk": sum(
                1 for d in decisions if d.approval_status not in APPROVED_STATUSES
            ),
        }
        logger.info(
            "[pipeline] run %s: %s candidates -> %s approved",
            run_id,
            counts["scanned_candidates"],
            counts["approved"],
        )
        return PipelineResult(
            run_id=run_id,
            scanner_output=scanner_output,
            decisions=decisions,
            counts=counts,
        )


def _sentiment_label(score: Optional[float]) -> str:
    if score is None:
        return "NEUTRAL"
    if score >= 0.6:
        return "POSITIVE"
    if score <= 0.4:
        return "NEGATIVE"
    return "NEUTRAL"


def _build_decision(candidate: Any, research: Dict[str, Any], risk: Dict[str, Any]) -> PipelineDecision:
    shares = int(getattr(candidate, "position_shares", 0) or 0)
    multiplier = float(risk.get("position_size_multiplier", 1.0) or 0.0)
    return PipelineDecision(
        symbol=getattr(candidate, "symbol", ""),
        sector=getattr(candidate, "sector", "Unknown"),
        pattern=getattr(candidate, "pattern", ""),
        entry=float(getattr(candidate, "entry", 0.0) or 0.0),
        stop=float(getattr(candidate, "stop", 0.0) or 0.0),
        target=float(getattr(candidate, "target", 0.0) or 0.0),
        rr_ratio=float(getattr(candidate, "rr_ratio", 0.0) or 0.0),
        position_shares=shares,
        approval_status=risk.get("approval_status", "UNKNOWN"),
        position_size_multiplier=multiplier,
        adjusted_shares=int(shares * multiplier),
        sentiment_score=research.get("sentiment_score"),
        research_confidence=research.get("confidence_score"),
        risk_confidence=risk.get("confidence_after_challenge"),
        concerns=list(risk.get("concerns", []) or []),
        red_flags=list(research.get("red_flags", []) or []),
        research_summary=research.get("research_summary", ""),
        decision_reasoning=risk.get("decision_reasoning", ""),
    )
