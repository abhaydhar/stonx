"""
Offline, deterministic tests for the Risk Agent (RISK-01..RISK-03).

No network, no crewai/langchain. Synthetic price frames are built with seeded
numpy so the metrics are reproducible. Candidates are aligned to their frame's
own ATR / last close, so the ATR-based stop checks are scale-consistent and not
seed-fragile.

Run:
    ./.venv/Scripts/python.exe -m pytest tests/test_risk_agent.py -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root on path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.llm import DeterministicLLM, FakeLLM
from agents.risk_agent import (
    APPROVED,
    CONDITIONAL,
    FAIL,
    PASS,
    REJECTED,
    AdjustedSize,
    RiskAgent,
    RiskDecision,
    apply_size_multiplier,
)
from tools.risk_tools import (
    RiskMetrics,
    annualized_volatility,
    average_true_range,
    beta,
    compute_risk_metrics,
    correlation,
    max_drawdown,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _frame_from_close(close: np.ndarray, noise: float = 2.0) -> pd.DataFrame:
    """Build an OHLCV frame around a close-price array."""
    close = np.asarray(close, dtype="float64")
    n = len(close)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + noise,
            "Low": close - noise,
            "Close": close,
            "Volume": np.full(n, 100_000.0),
        }
    )


def _calm_frame(n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Low-volatility, gently rising series (~6% annualized vol)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0003, 0.004, n)
    close = 1000.0 * np.cumprod(1 + rets)
    return _frame_from_close(close, noise=2.0)


def _noisy_frame(n: int = 200, seed: int = 11) -> pd.DataFrame:
    """Very high volatility (~95% annualized, well past max_annual_vol)."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.06, n)
    close = 1000.0 * np.cumprod(1 + rets)
    return _frame_from_close(close, noise=25.0)


def _moderate_vol_frame(n: int = 200, seed: int = 3) -> pd.DataFrame:
    """Vol between high_vol_threshold (0.45) and max_annual_vol (0.60), ~50%."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.032, n)
    close = 1000.0 * np.cumprod(1 + rets)
    return _frame_from_close(close, noise=12.0)


class _Candidate:
    """Minimal ScannerCandidate-like object (attribute access)."""

    def __init__(self, symbol, entry, stop, target, rr_ratio, confidence=0.8, sector="IT"):
        self.symbol = symbol
        self.entry = entry
        self.stop = stop
        self.target = target
        self.rr_ratio = rr_ratio
        self.confidence = confidence
        self.sector = sector


def _aligned_candidate(frame, symbol="CLEAN.NS", rr_ratio=3.5, confidence=0.8, stop_atrs=2.0):
    """A candidate whose stop sits a safe 2xATR below the frame's last close.

    stop distance == stop_atrs * ATR, so it lands squarely inside the
    [stop_atr_min, stop_atr_max] band -> stop validation always PASSes here.
    """
    atr = average_true_range(frame)
    entry = float(frame["Close"].iloc[-1])
    stop = entry - stop_atrs * atr
    target = entry + stop_atrs * atr * rr_ratio
    return _Candidate(symbol, entry=entry, stop=stop, target=target,
                      rr_ratio=rr_ratio, confidence=confidence)


# ---------------------------------------------------------------------------
# RISK-01 — risk_tools
# ---------------------------------------------------------------------------

def test_volatility_noisy_greater_than_calm():
    calm = annualized_volatility(_calm_frame()["Close"])
    noisy = annualized_volatility(_noisy_frame()["Close"])
    assert calm > 0.0
    assert noisy > calm


def test_volatility_empty_and_short_guarded():
    assert annualized_volatility(pd.Series([], dtype="float64")) == 0.0
    assert annualized_volatility(pd.Series([100.0])) == 0.0


def test_beta_identical_is_one():
    close = _calm_frame(seed=21)["Close"]
    assert beta(close, close) == pytest.approx(1.0, abs=1e-9)


def test_beta_degenerate_is_zero():
    flat = pd.Series([100.0] * 50)
    assert beta(_calm_frame()["Close"], flat) == 0.0


def test_correlation_identical_and_inverted():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 200)
    a = 100.0 * np.cumprod(1 + r)
    b = 100.0 * np.cumprod(1 - r)   # returns are the exact negative of a's
    a_s = pd.Series(a)
    b_s = pd.Series(b)
    assert correlation(a_s, a_s) == pytest.approx(1.0, abs=1e-9)
    assert correlation(a_s, b_s) == pytest.approx(-1.0, abs=1e-6)


def test_atr_positive_and_guarded():
    assert average_true_range(_calm_frame()) > 0.0
    # missing High/Low columns -> guarded 0.0
    assert average_true_range(pd.DataFrame({"Close": [1, 2, 3]})) == 0.0


def test_max_drawdown_range_and_handcheck():
    # Monotonic down from 100 to 50 -> drawdown exactly 0.5.
    close = pd.Series(np.linspace(100.0, 50.0, 11))
    mdd = max_drawdown(close)
    assert 0.0 <= mdd <= 1.0
    assert mdd == pytest.approx(0.5, abs=1e-9)
    # Monotonic up -> no drawdown.
    assert max_drawdown(pd.Series(np.linspace(50.0, 100.0, 11))) == pytest.approx(0.0, abs=1e-9)


def test_compute_risk_metrics_bundle():
    df = _calm_frame(seed=5)
    bench = _calm_frame(seed=6)
    metrics = compute_risk_metrics(df, benchmark=bench)
    assert isinstance(metrics, RiskMetrics)
    d = metrics.to_dict()
    assert set(d) == {"annualized_volatility", "beta", "correlation", "atr", "max_drawdown"}
    assert d["annualized_volatility"] > 0.0
    assert d["atr"] > 0.0
    assert 0.0 <= d["max_drawdown"] <= 1.0
    # Self-benchmark beta == 1.
    assert compute_risk_metrics(df, benchmark=df).beta == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# RISK-02 — RiskAgent.challenge
# ---------------------------------------------------------------------------

def test_clean_candidate_approved():
    agent = RiskAgent()
    frame = _calm_frame()
    research = {"sentiment_score": 0.8, "confidence_score": 0.85, "red_flags": []}
    decision = agent.challenge(_aligned_candidate(frame), research=research, price_data=frame)
    assert decision.approval_status == APPROVED
    assert decision.position_size_multiplier == 1.0
    assert decision.stop_loss_validation == PASS
    assert decision.concerns == []
    assert 0.0 <= decision.confidence_after_challenge <= 1.0


def test_red_flag_fraud_rejected():
    agent = RiskAgent()
    frame = _calm_frame()
    research = {"sentiment_score": 0.7, "confidence_score": 0.8, "red_flags": ["SEBI fraud probe"]}
    decision = agent.challenge(_aligned_candidate(frame), research=research, price_data=frame)
    assert decision.approval_status == REJECTED
    assert decision.position_size_multiplier == 0.0
    assert any("fraud" in c.lower() for c in decision.concerns)


def test_single_noncritical_red_flag_conditional():
    agent = RiskAgent()
    frame = _calm_frame()
    research = {"sentiment_score": 0.7, "confidence_score": 0.8, "red_flags": ["margin pressure"]}
    decision = agent.challenge(_aligned_candidate(frame), research=research, price_data=frame)
    assert decision.approval_status == CONDITIONAL
    assert decision.position_size_multiplier < 1.0
    assert decision.stop_loss_validation == PASS


def test_high_volatility_conditional_trimmed():
    agent = RiskAgent()
    frame = _moderate_vol_frame()
    decision = agent.challenge(_aligned_candidate(frame, symbol="MOD.NS"), price_data=frame)
    assert decision.stop_loss_validation == PASS
    assert decision.approval_status == CONDITIONAL
    assert decision.position_size_multiplier < 1.0


def test_extreme_volatility_rejected():
    agent = RiskAgent()
    frame = _noisy_frame()
    decision = agent.challenge(_aligned_candidate(frame, symbol="WILD.NS"), price_data=frame)
    assert decision.approval_status == REJECTED
    assert decision.position_size_multiplier == 0.0


def test_stop_above_entry_fails_and_rejected():
    agent = RiskAgent()
    bad = _Candidate("BAD.NS", entry=100.0, stop=105.0, target=120.0, rr_ratio=3.0)
    decision = agent.challenge(bad, price_data=_calm_frame())
    assert decision.stop_loss_validation == FAIL
    assert decision.approval_status == REJECTED
    assert any("stop" in c.lower() for c in decision.concerns)


def test_stop_too_tight_fails_on_atr():
    agent = RiskAgent()
    frame = _calm_frame()
    atr = average_true_range(frame)
    entry = float(frame["Close"].iloc[-1])
    # Stop only 0.1*ATR away -> below stop_atr_min (0.5).
    stop = entry - 0.1 * atr
    cand = _Candidate("TIGHT.NS", entry=entry, stop=stop, target=entry + 5 * atr, rr_ratio=3.0)
    decision = agent.challenge(cand, price_data=frame)
    assert decision.stop_loss_validation == FAIL
    assert decision.approval_status == REJECTED


def test_dict_candidate_supported():
    agent = RiskAgent()
    frame = _calm_frame()
    atr = average_true_range(frame)
    entry = float(frame["Close"].iloc[-1])
    cand = {
        "symbol": "DICT.NS",
        "entry_price": entry,
        "stop_price": entry - 2.0 * atr,
        "target_price": entry + 7.0 * atr,
        "rr_ratio": 3.5,
        "confidence": 0.75,
        "sector": "Auto",
    }
    decision = agent.challenge(cand, price_data=frame)
    assert decision.symbol == "DICT.NS"
    assert decision.approval_status == APPROVED
    assert decision.stop_loss_validation == PASS


def test_weak_sentiment_conditional_half_size():
    agent = RiskAgent()
    frame = _calm_frame()
    research = {"sentiment_score": 0.2, "confidence_score": 0.8, "red_flags": []}
    decision = agent.challenge(_aligned_candidate(frame), research=research, price_data=frame)
    assert decision.approval_status == CONDITIONAL
    assert decision.position_size_multiplier == pytest.approx(0.5, abs=1e-9)


def test_llm_reasoning_override():
    fake = FakeLLM(response="LLM says: proceed with caution.")
    agent = RiskAgent(llm_client=fake)
    frame = _calm_frame()
    decision = agent.challenge(_aligned_candidate(frame), price_data=frame)
    assert decision.decision_reasoning == "LLM says: proceed with caution."
    assert fake.calls  # llm was actually invoked


def test_deterministic_reasoning_default():
    agent = RiskAgent(llm_client=DeterministicLLM())
    frame = _calm_frame()
    decision = agent.challenge(_aligned_candidate(frame), price_data=frame)
    assert decision.symbol in decision.decision_reasoning
    assert APPROVED in decision.decision_reasoning


# ---------------------------------------------------------------------------
# RISK-03 — sizing helper + batch
# ---------------------------------------------------------------------------

def test_apply_size_multiplier_shares():
    result = apply_size_multiplier(100, 0.5)
    assert isinstance(result, AdjustedSize)
    assert result.shares == 50
    assert result.original_shares == 100
    assert isinstance(result.reason, str) and result.reason


def test_apply_size_multiplier_floors_and_clamps():
    assert apply_size_multiplier(101, 0.5).shares == 50   # floor(50.5)
    assert apply_size_multiplier(100, 2.0).shares == 100  # clamp to 1.0
    assert apply_size_multiplier(100, -1.0).shares == 0   # clamp to 0.0


def test_apply_size_multiplier_on_riskresult_like():
    class _RR:
        position_size_shares = 200
        position_size_inr = 40000.0
    adj = apply_size_multiplier(_RR(), 0.25)
    assert adj.shares == 50
    assert adj.position_inr == pytest.approx(10000.0, abs=1e-6)


def test_challenge_batch():
    agent = RiskAgent()
    calm = _calm_frame()
    candidates = [
        _aligned_candidate(calm, symbol="CLEAN.NS"),
        _Candidate("BAD.NS", entry=100.0, stop=105.0, target=120.0, rr_ratio=3.0),
    ]
    research_map = {"CLEAN.NS": {"sentiment_score": 0.8, "confidence_score": 0.8, "red_flags": []}}
    price_map = {"CLEAN.NS": calm, "BAD.NS": calm}
    decisions = agent.challenge_batch(candidates, research_map=research_map, price_map=price_map)
    assert len(decisions) == 2
    by_symbol = {d.symbol: d for d in decisions}
    assert by_symbol["CLEAN.NS"].approval_status == APPROVED
    assert by_symbol["BAD.NS"].approval_status == REJECTED


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------

def test_to_dict_contract_keys():
    agent = RiskAgent()
    frame = _calm_frame()
    decision = agent.challenge(_aligned_candidate(frame), price_data=frame)
    d = decision.to_dict()
    assert set(d) == {
        "symbol", "approval_status", "concerns", "position_size_multiplier",
        "stop_loss_validation", "confidence_after_challenge", "decision_reasoning",
    }
    assert d["approval_status"] in {APPROVED, REJECTED, CONDITIONAL}
    assert 0.0 <= d["position_size_multiplier"] <= 1.0
    assert 0.0 <= d["confidence_after_challenge"] <= 1.0
    assert d["stop_loss_validation"] in {PASS, FAIL}
    assert isinstance(d["concerns"], list)
