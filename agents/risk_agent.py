"""
RISK-02 / RISK-03 — Adversarial Risk Agent.

The deterministic :class:`modules.risk.RiskManager` already enforces the hard
gate (R:R minimum, fixed-fractional sizing, portfolio heat / sector limits).
The Risk *Agent* is the adversarial second opinion that sits on top of a
candidate which has *already passed* that gate: it re-validates the stop, hunts
for reasons the trade could still be wrong (research red flags, weak sentiment,
excessive volatility, marginal R:R / confidence) and outputs a size multiplier
plus a final verdict.

The agent runs fully offline. It accepts an optional ``llm_client`` conforming
to :class:`agents.llm.LLMClient`; with the default :class:`DeterministicLLM`
the reasoning string is produced deterministically. A real / fake client whose
``complete()`` returns non-empty text may replace that reasoning. No crewai /
langchain import happens at module import time.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from agents.llm import DeterministicLLM, LLMClient
from tools.risk_tools import annualized_volatility, average_true_range

logger = logging.getLogger(__name__)

APPROVED = "APPROVED"
REJECTED = "REJECTED"
CONDITIONAL = "CONDITIONAL"
_ALLOWED_STATUS = {APPROVED, REJECTED, CONDITIONAL}

PASS = "PASS"
FAIL = "FAIL"


# ---------------------------------------------------------------------------
# Small getattr/dict accessors
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict or an object (works for both candidate shapes)."""

    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _num(value: Any, default: float = 0.0) -> float:
    """Best-effort float coercion."""

    try:
        if value is None:
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Decision + size dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RiskDecision:
    """Matches the PRD "Risk Decision" contract."""

    symbol: str
    approval_status: str = APPROVED          # APPROVED | REJECTED | CONDITIONAL
    concerns: List[str] = field(default_factory=list)
    position_size_multiplier: float = 1.0    # 0..1
    stop_loss_validation: str = PASS         # PASS | FAIL
    confidence_after_challenge: float = 0.5  # 0..1
    decision_reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "approval_status": self.approval_status,
            "concerns": list(self.concerns),
            "position_size_multiplier": self.position_size_multiplier,
            "stop_loss_validation": self.stop_loss_validation,
            "confidence_after_challenge": self.confidence_after_challenge,
            "decision_reasoning": self.decision_reasoning,
        }


@dataclass
class AdjustedSize:
    """Result of :func:`apply_size_multiplier` (RISK-03)."""

    shares: int
    original_shares: int
    multiplier: float
    position_inr: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shares": self.shares,
            "original_shares": self.original_shares,
            "multiplier": self.multiplier,
            "position_inr": self.position_inr,
            "reason": self.reason,
        }


def apply_size_multiplier(risk_result_or_shares: Any, multiplier: float) -> AdjustedSize:
    """RISK-03 — scale a position by ``multiplier`` (floored to whole shares).

    Accepts either a raw share count (int/float) or a ``RiskResult``-like object
    exposing ``position_size_shares`` (+ optional ``position_size_inr``). Always
    returns an :class:`AdjustedSize` carrying the adjusted share count and a
    human-readable reason. The multiplier is clamped to ``[0, 1]``.
    """

    mult = _clamp01(_num(multiplier, 0.0))

    if isinstance(risk_result_or_shares, bool):  # guard: bool is an int subclass
        original = 0
        position_inr = 0.0
    elif isinstance(risk_result_or_shares, (int, float)):
        original = int(risk_result_or_shares)
        position_inr = 0.0
    else:
        original = int(_num(_get(risk_result_or_shares, "position_size_shares",
                                 _get(risk_result_or_shares, "shares", 0))))
        position_inr = _num(_get(risk_result_or_shares, "position_size_inr",
                                 _get(risk_result_or_shares, "position_inr", 0.0)))

    adjusted = int(math.floor(max(0, original) * mult))

    if original > 0 and position_inr > 0:
        per_share = position_inr / original
        adjusted_inr = round(per_share * adjusted, 2)
    else:
        adjusted_inr = 0.0

    reason = (
        f"Applied risk multiplier x{mult:.2f}: {original} -> {adjusted} shares"
        + (f" (₹{adjusted_inr:,.0f})" if adjusted_inr else "")
        + "."
    )
    return AdjustedSize(
        shares=adjusted,
        original_shares=max(0, original),
        multiplier=mult,
        position_inr=adjusted_inr,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Risk Agent
# ---------------------------------------------------------------------------

class RiskAgent:
    """Adversarial risk reviewer for candidates that passed the deterministic gate.

    Thresholds (all tunable via the constructor):
      * ``max_annual_vol`` (0.60) — annualized volatility above this is an
        automatic REJECT ("too dangerous to size at all").
      * ``high_vol_threshold`` (0.45) — volatility above this but below the max
        is tolerated but downsized proportionally (multiplier x
        ``high_vol_threshold / vol``) and flips the verdict to CONDITIONAL.
      * ``stop_atr_min`` (0.5) — a stop closer than this many ATRs is "too
        tight" (noise will stop it out) -> stop validation FAIL.
      * ``stop_atr_max`` (4.0) — a stop farther than this many ATRs is "too
        wide" (oversized risk-per-share) -> stop validation FAIL.
      * ``min_rr`` (2.5) — R:R below ``min_rr * marginal_rr_band`` is "barely
        above minimum" -> size trimmed.
      * ``low_confidence`` (0.5) — pattern confidence below this trims size.
      * ``weak_sentiment`` (0.35) — research sentiment below this halves size
        and flips to CONDITIONAL.
    """

    # Terms that make a single research red flag fatal on their own.
    CRITICAL_FLAG_TERMS = (
        "fraud", "default", "probe", "investigation", "scam",
        "sebi", "insolvency", "bankruptcy", "delist", "forensic",
    )
    # Number of (non-critical) red flags that becomes fatal in aggregate.
    MANY_FLAGS = 3
    # Weight by which each concern erodes post-challenge confidence.
    CONCERN_PENALTY = 0.08
    # R:R within this multiple of the minimum is "marginal".
    MARGINAL_RR_BAND = 1.2

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        max_annual_vol: float = 0.60,
        high_vol_threshold: float = 0.45,
        stop_atr_min: float = 0.5,
        stop_atr_max: float = 4.0,
        min_rr: float = 2.5,
        low_confidence: float = 0.5,
        weak_sentiment: float = 0.35,
    ):
        self.llm: LLMClient = llm_client or DeterministicLLM()
        self.max_annual_vol = max_annual_vol
        self.high_vol_threshold = high_vol_threshold
        self.stop_atr_min = stop_atr_min
        self.stop_atr_max = stop_atr_max
        self.min_rr = min_rr
        self.low_confidence = low_confidence
        self.weak_sentiment = weak_sentiment

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def challenge(
        self,
        candidate: Any,
        research: Any = None,
        price_data: Optional[pd.DataFrame] = None,
        benchmark: Optional[pd.DataFrame] = None,
        portfolio: Any = None,
    ) -> RiskDecision:
        """Adversarially review a single candidate and return a RiskDecision."""

        symbol = str(_get(candidate, "symbol", "UNKNOWN"))
        entry = _num(_get(candidate, "entry", _get(candidate, "entry_price", 0.0)))
        stop = _num(_get(candidate, "stop", _get(candidate, "stop_price", 0.0)))
        target = _num(_get(candidate, "target", _get(candidate, "target_price", 0.0)))
        rr_ratio = _num(_get(candidate, "rr_ratio", 0.0))
        base_confidence = _num(_get(candidate, "confidence", 0.5), 0.5)

        red_flags = _get(research, "red_flags", None) or []
        if not isinstance(red_flags, (list, tuple)):
            red_flags = [red_flags]
        sentiment = _get(research, "sentiment_score", None)
        research_conf = _get(research, "confidence_score", None)

        concerns: List[str] = []
        fatal = False
        multiplier = 1.0

        # --- Stop-loss validation ------------------------------------------------
        stop_valid = True
        if stop >= entry:
            stop_valid = False
            concerns.append(
                f"stop {stop:.2f} >= entry {entry:.2f}: no downside protection"
            )
        if target <= entry:
            stop_valid = False
            concerns.append(
                f"target {target:.2f} <= entry {entry:.2f}: no upside to capture"
            )

        atr = 0.0
        if price_data is not None:
            atr = average_true_range(price_data)
            if atr > 0 and stop < entry:
                stop_distance = entry - stop
                if stop_distance < self.stop_atr_min * atr:
                    stop_valid = False
                    concerns.append(
                        f"stop distance {stop_distance:.2f} < "
                        f"{self.stop_atr_min:g}xATR ({self.stop_atr_min * atr:.2f}): "
                        f"too tight, noise will trigger it"
                    )
                elif stop_distance > self.stop_atr_max * atr:
                    stop_valid = False
                    concerns.append(
                        f"stop distance {stop_distance:.2f} > "
                        f"{self.stop_atr_max:g}xATR ({self.stop_atr_max * atr:.2f}): "
                        f"too wide, risk-per-share is oversized"
                    )

        if not stop_valid:
            fatal = True

        # --- Research red flags --------------------------------------------------
        if red_flags:
            for flag in red_flags:
                concerns.append(f"research red flag: {flag}")
            critical = [
                f for f in red_flags
                if any(term in str(f).lower() for term in self.CRITICAL_FLAG_TERMS)
            ]
            if critical or len(red_flags) >= self.MANY_FLAGS:
                fatal = True
                concerns.append(
                    f"{'critical' if critical else 'multiple'} red flag(s) "
                    f"({len(red_flags)}) -> reject"
                )
            else:
                multiplier *= 0.5

        # --- Weak sentiment ------------------------------------------------------
        if sentiment is not None:
            sval = _num(sentiment, 1.0)
            if sval < self.weak_sentiment:
                concerns.append(
                    f"weak research sentiment {sval:.2f} < {self.weak_sentiment:.2f}"
                )
                multiplier *= 0.5

        # --- Volatility ----------------------------------------------------------
        ann_vol = 0.0
        if price_data is not None:
            ann_vol = annualized_volatility(price_data)
            if ann_vol > self.max_annual_vol:
                fatal = True
                concerns.append(
                    f"annualized volatility {ann_vol:.0%} > max "
                    f"{self.max_annual_vol:.0%} -> reject"
                )
            elif ann_vol > self.high_vol_threshold:
                scale = self.high_vol_threshold / ann_vol
                multiplier *= scale
                concerns.append(
                    f"high volatility {ann_vol:.0%} > {self.high_vol_threshold:.0%}: "
                    f"size trimmed x{scale:.2f}"
                )

        # --- Marginal R:R --------------------------------------------------------
        if 0 < rr_ratio < self.min_rr * self.MARGINAL_RR_BAND:
            concerns.append(
                f"R:R {rr_ratio:.2f} only marginally above minimum {self.min_rr:.1f}"
            )
            multiplier *= 0.8

        # --- Low pattern confidence ---------------------------------------------
        if base_confidence < self.low_confidence:
            concerns.append(
                f"low pattern confidence {base_confidence:.2f} < {self.low_confidence:.2f}"
            )
            multiplier *= 0.8

        # --- Verdict -------------------------------------------------------------
        if fatal:
            status = REJECTED
            multiplier = 0.0
        elif concerns:
            status = CONDITIONAL
        else:
            status = APPROVED
        multiplier = _clamp01(multiplier)

        # --- Post-challenge confidence ------------------------------------------
        start_conf = _num(research_conf, base_confidence) if research_conf is not None else base_confidence
        confidence = start_conf - self.CONCERN_PENALTY * len(concerns)
        if fatal:
            confidence = min(confidence, 0.2)
        confidence = round(_clamp01(confidence), 4)
        multiplier = round(multiplier, 4)

        stop_validation = PASS if stop_valid else FAIL
        reasoning = self._build_reasoning(
            symbol=symbol,
            status=status,
            concerns=concerns,
            multiplier=multiplier,
            rr_ratio=rr_ratio,
            stop_validation=stop_validation,
            ann_vol=ann_vol,
            atr=atr,
            confidence=confidence,
        )

        return RiskDecision(
            symbol=symbol,
            approval_status=status,
            concerns=concerns,
            position_size_multiplier=multiplier,
            stop_loss_validation=stop_validation,
            confidence_after_challenge=confidence,
            decision_reasoning=reasoning,
        )

    def challenge_batch(
        self,
        candidates: Any,
        research_map: Optional[Mapping[str, Any]] = None,
        price_map: Optional[Mapping[str, pd.DataFrame]] = None,
    ) -> List[RiskDecision]:
        """Challenge many candidates, keyed by symbol for research / price lookup."""

        research_map = research_map or {}
        price_map = price_map or {}
        decisions: List[RiskDecision] = []
        for candidate in candidates:
            symbol = _get(candidate, "symbol", None)
            research = research_map.get(symbol) if symbol is not None else None
            price = price_map.get(symbol) if symbol is not None else None
            decisions.append(self.challenge(candidate, research=research, price_data=price))
        return decisions

    # ------------------------------------------------------------------
    # Reasoning
    # ------------------------------------------------------------------

    def _build_reasoning(
        self,
        symbol: str,
        status: str,
        concerns: List[str],
        multiplier: float,
        rr_ratio: float,
        stop_validation: str,
        ann_vol: float,
        atr: float,
        confidence: float,
    ) -> str:
        """Deterministic verdict sentence, optionally overridden by the LLM."""

        top = concerns[:3]
        deterministic = (
            f"{symbol}: {status} after adversarial review "
            f"(R:R {rr_ratio:.2f}, size x{multiplier:.2f}, stop {stop_validation}, "
            f"post-challenge confidence {confidence:.2f})."
        )
        if top:
            deterministic += " Top concerns: " + "; ".join(top) + "."
        else:
            deterministic += " No material concerns survived the challenge."

        # Optional LLM augmentation. DeterministicLLM returns "" -> keep the
        # deterministic sentence. Any failure degrades to deterministic too.
        try:
            system = (
                "You are a skeptical risk manager. Summarize the risk verdict in "
                "one or two sentences. Do not invent facts beyond those given."
            )
            prompt = (
                f"Symbol: {symbol}\nVerdict: {status}\n"
                f"Size multiplier: {multiplier:.2f}\nR:R: {rr_ratio:.2f}\n"
                f"Stop validation: {stop_validation}\n"
                f"Annualized volatility: {ann_vol:.2%}\nATR: {atr:.2f}\n"
                f"Concerns: {concerns}\n"
                "Write the final decision_reasoning."
            )
            llm_text = self.llm.complete(prompt, system=system)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[risk-agent] llm reasoning failed: %s", exc)
            llm_text = ""

        if isinstance(llm_text, str) and llm_text.strip():
            return llm_text.strip()
        return deterministic
