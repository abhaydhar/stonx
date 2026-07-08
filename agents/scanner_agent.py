"""
Scanner Agent
Wraps a CrewAI Agent that runs the 5-stage technical scan pipeline:
  Stage 1 – Data Ingestion       (fetch_ohlcv_tool, fetch_universe_tool)
  Stage 2 – Fundamental Filter   (screen_fundamentals_tool)
  Stage 3 – Pattern Detection    (detect_patterns_tool)
  Stage 4 – Volume Profile       (calculate_volume_profile_tool)
  Stage 5 – Risk/Reward Gate     (validate_risk_reward_tool)

Also checks market regime (check_market_regime_tool) before scanning.

The CrewAI agent is given a structured task description that guides it
through each stage.  Tool outputs are JSON strings that the LLM reads
and reasons about to produce a final shortlist.
"""

import logging
from typing import List

from crewai import Agent, Task

from agents.base import BaseAgent
from tools.data_tools import (
    fetch_ohlcv_tool,
    fetch_universe_tool,
    screen_fundamentals_tool,
)
from tools.analysis_tools import (
    detect_patterns_tool,
    calculate_volume_profile_tool,
    validate_risk_reward_tool,
    check_market_regime_tool,
)

logger = logging.getLogger(__name__)

# All tools available to the Scanner Agent
SCANNER_TOOLS = [
    fetch_universe_tool,
    fetch_ohlcv_tool,
    screen_fundamentals_tool,
    detect_patterns_tool,
    calculate_volume_profile_tool,
    validate_risk_reward_tool,
    check_market_regime_tool,
]


class ScannerAgent(BaseAgent):
    """
    Scanner Agent: finds high-probability trade setups in the NSE universe.

    Default model: claude-haiku-4-5 (fast + cost-effective for rule-based work)
    """

    ROLE = "Senior Technical Scanner"
    GOAL = (
        "Identify 5–10 high-probability NSE trade setups that satisfy "
        "fundamental quality, strong technical patterns, volume confirmation, "
        "and a minimum risk/reward ratio of 2.5x."
    )
    BACKSTORY = (
        "You are an expert technical analyst with 10 years of experience "
        "trading NSE equities.  You follow a disciplined 5-stage pipeline: "
        "first filter for quality fundamentals, then identify stocks forming "
        "consolidation breakouts, higher-low patterns, or range compression.  "
        "You always confirm with volume and validate that the risk/reward is "
        "at least 2.5x before recommending a trade.  You are methodical, "
        "data-driven, and never recommend more than 10 setups at once."
    )

    TASK_DESCRIPTION = """
You are scanning the NSE stock universe to find the best trade setups for today.

Follow this exact pipeline:

**Step 0 — Market Regime**
Use check_market_regime_tool to determine if we are in a bull or bear market.
Adjust minimum R:R accordingly (2.5x bull, 3.5x bear).

**Step 1 — Get Universe**
Use fetch_universe_tool to retrieve the list of NSE stocks to scan.

**Step 2 — Fundamental Filter**
For EACH symbol, use screen_fundamentals_tool.
Skip any symbol that fails (PASS=false).  Keep a running list of passed symbols.

**Step 3 — Pattern Detection**
For each fundamentally-approved symbol, use detect_patterns_tool.
Only keep symbols where 'passed' = true (at least one pattern detected).
Record the best_pattern and confidence for each.

**Step 4 — Volume Profile**
For each pattern-confirmed symbol, use calculate_volume_profile_tool.
Extract hvn_support (stop loss level) and the first lvn_target (price target).
Skip symbols where hvn_support or lvn_targets are missing.

**Step 5 — Risk/Reward Validation**
For each remaining symbol, use validate_risk_reward_tool with:
  entry_price  = current price (from volume profile output)
  stop_price   = hvn_support
  target_price = first lvn_target
Only keep symbols where approved = true.

**Final Output**
Produce a ranked JSON list (best setups first) with this structure per trade:
{
  "rank": 1,
  "symbol": "...",
  "pattern": "...",
  "confidence": 0.0,
  "entry": 0.0,
  "stop": 0.0,
  "target": 0.0,
  "rr_ratio": 0.0,
  "position_shares": 0,
  "position_inr": 0.0,
  "capital_at_risk_inr": 0.0,
  "rationale": "One sentence explaining why this is a good setup."
}

Output ONLY valid JSON — a list of trade objects.
Limit to the top 10 setups maximum.
"""

    EXPECTED_OUTPUT = (
        "A JSON list of 5–10 approved trade setups, each with symbol, pattern, "
        "entry/stop/target prices, R:R ratio, position size, and a brief rationale."
    )

    def __init__(self, model: str = "claude-haiku-4-5"):
        super().__init__(model=model, tools=SCANNER_TOOLS)
        self._crewai_agent = self._build_crewai_agent()
        self._task = self._build_task()

    def _build_crewai_agent(self) -> Agent:
        return Agent(
            role=self.ROLE,
            goal=self.GOAL,
            backstory=self.BACKSTORY,
            tools=SCANNER_TOOLS,
            llm=self.llm,
            verbose=True,
            allow_delegation=False,
        )

    def _build_task(self) -> Task:
        return Task(
            description=self.TASK_DESCRIPTION,
            agent=self._crewai_agent,
            expected_output=self.EXPECTED_OUTPUT,
        )

    @property
    def crewai_agent(self) -> Agent:
        """Return the underlying CrewAI Agent (used by orchestrator)."""
        return self._crewai_agent

    @property
    def task(self) -> Task:
        """Return the scan task (used by orchestrator)."""
        return self._task
