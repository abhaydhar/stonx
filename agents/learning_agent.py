"""
Learning Agent (PRD Wave 7, LEARN-02..LEARN-04).

Turns closed-trade outcome analytics (:mod:`modules.learning`) into evidence-
backed, human-approved configuration recommendations. The agent NEVER
auto-applies a change (``auto_apply`` is always ``False``): it only proposes.

Design constraints (shared with the other StockScanner agents):
  * Constructible and unit-testable with NO crewai / langchain / anthropic and
    NO network / API key. The optional LLM only *phrases* findings; all logic is
    deterministic. Defaults to :class:`agents.llm.DeterministicLLM`.
  * ``modules.backtest`` is imported lazily (inside methods) so importing this
    module stays cheap and side-effect free.

Recommendation contract (PRD "Learning Recommendation"):
    {
        "finding": str,
        "action": str,
        "config_change": {...},
        "backtest_validation": {"before": {"sharpe", "expectancy"},
                                 "after":  {"sharpe", "expectancy"}},
        "auto_apply": False,
    }

Heuristic rules (deterministic, documented)
-------------------------------------------
R1 - Under-performing pattern -> ``INCREASE_RR_THRESHOLD``:
     for every pattern with at least ``min_pattern_trades`` trades whose
     per-pattern ``expectancy`` is <= ``pattern_expectancy_floor`` (default 0,
     i.e. flat/negative), demand a higher R:R for that pattern via
     ``{"MIN_RR": {<pattern>: base_min_rr + rr_bump}}``. Worst pattern first.
R2 - Low overall win rate -> ``RAISE_VOLUME_CONFIRMATION``:
     if the overall ``win_rate`` is below ``low_win_rate_threshold`` (default
     0.40), require a stronger volume spike for confirmation via
     ``{"VOLUME_SPIKE_MULTIPLIER": base_vsm + vsm_bump}``.

Backtest validation (LEARN-04)
------------------------------
When a ``backtester`` and ``price_data`` are supplied, each recommendation gets
a ``backtest_validation`` = before/after ``{sharpe, expectancy}`` obtained by
running the backtester with the base config vs a config reflecting the change.
The backtester exposes a single global ``min_rr`` knob, so a per-pattern
``MIN_RR`` change is applied globally (using the max proposed value) as a proxy.
When no backtester is supplied, ``backtest_validation`` stays ``{}`` and a note
records that validation is pending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.llm import DeterministicLLM, LLMClient
from modules.learning import LearningStats, analyze_trades

# Action labels (PRD contract "action" values).
INCREASE_RR_THRESHOLD = "INCREASE_RR_THRESHOLD"
RAISE_VOLUME_CONFIRMATION = "RAISE_VOLUME_CONFIRMATION"

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"


# ---------------------------------------------------------------------------
# Contract dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LearningRecommendation:
    """A single proposed config change matching the PRD contract."""

    finding: str
    action: str
    config_change: Dict[str, Any] = field(default_factory=dict)
    backtest_validation: Dict[str, Any] = field(default_factory=dict)
    auto_apply: bool = False  # never auto-applied; human approval required

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding": self.finding,
            "action": self.action,
            "config_change": self.config_change,
            "backtest_validation": self.backtest_validation,
            "auto_apply": self.auto_apply,
        }


@dataclass
class LearningReport:
    """Full learning output: status, sample size, stats, recommendations."""

    status: str                       # 'ok' | 'insufficient_data'
    trades_analyzed: int
    min_trades: int
    stats: Optional[LearningStats] = None
    recommendations: List[LearningRecommendation] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "trades_analyzed": self.trades_analyzed,
            "min_trades": self.min_trades,
            "stats": self.stats.to_dict() if self.stats is not None else None,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Config-value helpers
# ---------------------------------------------------------------------------

def _cfg_value(base_config: Any, names: List[str], default: float) -> float:
    """Read the first present attribute in ``names`` from a config object.

    Accepts both BacktestConfig (lower_snake) and ScannerConfig (UPPER) shapes.
    """
    if base_config is not None:
        for name in names:
            if hasattr(base_config, name):
                try:
                    return float(getattr(base_config, name))
                except (TypeError, ValueError):
                    continue
    return default


# ---------------------------------------------------------------------------
# Learning Agent
# ---------------------------------------------------------------------------

class LearningAgent:
    """Produces evidence-backed, human-approved config recommendations."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        min_trades: int = 50,
        *,
        min_pattern_trades: int = 5,
        pattern_expectancy_floor: float = 0.0,
        low_win_rate_threshold: float = 0.40,
        rr_bump: float = 1.0,
        vsm_bump: float = 0.5,
    ):
        self.llm = llm_client or DeterministicLLM()
        self.min_trades = int(min_trades)
        self.min_pattern_trades = int(min_pattern_trades)
        self.pattern_expectancy_floor = float(pattern_expectancy_floor)
        self.low_win_rate_threshold = float(low_win_rate_threshold)
        self.rr_bump = float(rr_bump)
        self.vsm_bump = float(vsm_bump)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        journal_or_trades: Any,
        backtester: Any = None,
        price_data: Any = None,
        base_config: Any = None,
    ) -> LearningReport:
        """Analyse closed trades and return a :class:`LearningReport`.

        ``journal_or_trades`` is either a TradeJournal-like object (exposing
        ``get_closed_trades()``) or a plain ``list[dict]`` of trades.
        """
        trades = self._extract_trades(journal_or_trades)
        n = len(trades)

        # LEARN-03: gate on minimum sample size.
        if n < self.min_trades:
            return LearningReport(
                status=STATUS_INSUFFICIENT,
                trades_analyzed=n,
                min_trades=self.min_trades,
                stats=None,
                recommendations=[],
                notes=f"need >= {self.min_trades} closed trades",
            )

        stats = analyze_trades(trades)
        recommendations = self._build_recommendations(stats, base_config)

        notes = ""
        # LEARN-04: validate each recommendation against the backtester.
        if backtester is not None and price_data:
            self._attach_backtest_validation(
                recommendations, backtester, price_data, base_config
            )
        else:
            notes = "backtest validation pending (no backtester/price_data provided)"

        return LearningReport(
            status=STATUS_OK,
            trades_analyzed=n,
            min_trades=self.min_trades,
            stats=stats,
            recommendations=recommendations,
            notes=notes,
        )

    def propose_config_changes(self, report: LearningReport) -> Dict[str, Any]:
        """Merge every recommendation's ``config_change`` (human still approves).

        Nested dict values (e.g. per-pattern ``MIN_RR``) are merged key-by-key;
        scalar values are overwritten last-write-wins.
        """
        merged: Dict[str, Any] = {}
        for rec in report.recommendations:
            for key, value in rec.config_change.items():
                if isinstance(value, dict):
                    existing = merged.get(key)
                    if isinstance(existing, dict):
                        existing.update(value)
                    else:
                        merged[key] = dict(value)
                else:
                    merged[key] = value
        return merged

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_trades(journal_or_trades: Any) -> List[Dict[str, Any]]:
        if journal_or_trades is None:
            return []
        if hasattr(journal_or_trades, "get_closed_trades"):
            return list(journal_or_trades.get_closed_trades())
        if isinstance(journal_or_trades, dict):
            return [journal_or_trades]
        return list(journal_or_trades)

    def _build_recommendations(
        self,
        stats: LearningStats,
        base_config: Any,
    ) -> List[LearningRecommendation]:
        base_min_rr = _cfg_value(base_config, ["min_rr", "MIN_RR"], 2.5)
        base_vsm = _cfg_value(
            base_config, ["volume_spike_multiplier", "VOLUME_SPIKE_MULTIPLIER"], 1.5
        )

        recommendations: List[LearningRecommendation] = []

        # R1: under-performing patterns (worst expectancy first, deterministic).
        underperformers = [
            (pattern, grp)
            for pattern, grp in stats.by_pattern.items()
            if grp.get("trades", 0) >= self.min_pattern_trades
            and grp.get("expectancy", 0.0) <= self.pattern_expectancy_floor
        ]
        underperformers.sort(key=lambda item: (item[1]["expectancy"], item[0]))
        for pattern, grp in underperformers:
            new_rr = round(base_min_rr + self.rr_bump, 2)
            default_finding = (
                f"Pattern '{pattern}' underperformed: expectancy "
                f"{grp['expectancy']:.2f}% over {grp['trades']} trades "
                f"(win rate {grp['win_rate']:.0%})."
            )
            recommendations.append(
                LearningRecommendation(
                    finding=self._phrase(default_finding, pattern, grp),
                    action=INCREASE_RR_THRESHOLD,
                    config_change={"MIN_RR": {pattern: new_rr}},
                )
            )

        # R2: low overall win rate.
        if stats.win_rate < self.low_win_rate_threshold:
            new_vsm = round(base_vsm + self.vsm_bump, 2)
            default_finding = (
                f"Overall win rate {stats.win_rate:.0%} is below "
                f"{self.low_win_rate_threshold:.0%}; require stronger volume "
                f"confirmation on breakouts."
            )
            recommendations.append(
                LearningRecommendation(
                    finding=self._phrase(default_finding, None, None),
                    action=RAISE_VOLUME_CONFIRMATION,
                    config_change={"VOLUME_SPIKE_MULTIPLIER": new_vsm},
                )
            )

        return recommendations

    def _phrase(
        self,
        default_finding: str,
        pattern: Optional[str],
        group: Optional[Dict[str, float]],
    ) -> str:
        """Optionally use the LLM to phrase a finding; fall back deterministically.

        :class:`DeterministicLLM` returns ``""`` so the deterministic default is
        always used unless a real / fake client returns non-empty text.
        """
        try:
            text = self.llm.complete(
                prompt=(
                    "Summarise this trading finding in one sentence. "
                    f"Pattern={pattern}, stats={group}. Default: {default_finding}"
                ),
                system="You are a concise trading performance analyst.",
            )
        except Exception:
            text = ""
        text = (text or "").strip()
        return text if text else default_finding

    def _attach_backtest_validation(
        self,
        recommendations: List[LearningRecommendation],
        backtester: Any,
        price_data: Any,
        base_config: Any,
    ) -> None:
        from dataclasses import replace

        from modules.backtest import BacktestConfig

        bt_base = (
            base_config
            if isinstance(base_config, BacktestConfig)
            else BacktestConfig.from_scanner_config(base_config)
        )

        before_metrics = backtester.run(price_data, bt_base).metrics
        before = {
            "sharpe": before_metrics.sharpe,
            "expectancy": before_metrics.expectancy,
        }

        for rec in recommendations:
            overrides = self._change_to_bt_overrides(bt_base, rec.config_change)
            after_cfg = replace(bt_base, **overrides) if overrides else bt_base
            after_metrics = backtester.run(price_data, after_cfg).metrics
            rec.backtest_validation = {
                "before": dict(before),
                "after": {
                    "sharpe": after_metrics.sharpe,
                    "expectancy": after_metrics.expectancy,
                },
            }

    @staticmethod
    def _change_to_bt_overrides(
        bt_base: Any,
        config_change: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Map a recommendation's config_change onto BacktestConfig fields.

        Per-pattern dict values collapse to their max (a conservative global
        proxy, since the backtester has a single global knob).
        """
        def _scalar(value: Any, fallback: float) -> float:
            if isinstance(value, dict):
                nums = [float(v) for v in value.values()]
                return max(nums) if nums else float(fallback)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(fallback)

        overrides: Dict[str, Any] = {}
        for key, value in config_change.items():
            if key == "MIN_RR":
                overrides["min_rr"] = _scalar(value, bt_base.min_rr)
            elif key == "VOLUME_SPIKE_MULTIPLIER":
                overrides["volume_spike_multiplier"] = _scalar(
                    value, bt_base.volume_spike_multiplier
                )
            elif key == "CONSOLIDATION_RANGE_PCT":
                overrides["consolidation_range_pct"] = _scalar(
                    value, bt_base.consolidation_range_pct
                )
        return overrides
