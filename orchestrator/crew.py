"""
StonxCrew — CrewAI orchestrator for MVP Phase 1 (Scanner Agent only).

Phase 1: Single-agent crew (Scanner Agent).
Future phases will add Research Agent, Risk Agent, etc.

Usage:
    from orchestrator.crew import StonxCrew
    crew = StonxCrew()
    result = crew.run()
    print(result)
"""

import json
import logging
from typing import Any, Dict, List, Optional

from crewai import Crew

from agents.scanner_agent import ScannerAgent

logger = logging.getLogger(__name__)


class StonxCrew:
    """
    Orchestrates all StockScanner agents via CrewAI.

    MVP (Phase 1): Scanner Agent only.
    Add Research / Risk agents in later phases by extending _build_crew().
    """

    def __init__(self, scanner_model: str = "claude-haiku-4-5", verbose: bool = True):
        self.verbose = verbose
        logger.info("[StonxCrew] Initialising agents...")
        self.scanner = ScannerAgent(model=scanner_model)
        self._crew = self._build_crew()
        logger.info("[StonxCrew] Crew ready.")

    def _build_crew(self) -> Crew:
        """Build CrewAI Crew with current agents and tasks."""
        return Crew(
            agents=[self.scanner.crewai_agent],
            tasks=[self.scanner.task],
            verbose=self.verbose,
        )

    def run(self) -> Dict[str, Any]:
        """
        Execute the full scan pipeline.

        Returns a dict with:
          - raw_output: the string returned by CrewAI
          - setups: parsed list of trade setups (or [] on parse failure)
          - error: error string if something went wrong (else None)
        """
        logger.info("[StonxCrew] Starting scan run...")

        try:
            raw_output = self._crew.kickoff()
        except Exception as exc:
            logger.error(f"[StonxCrew] Crew kickoff failed: {exc}", exc_info=True)
            return {"raw_output": "", "setups": [], "error": str(exc)}

        # Try to parse JSON from the agent output
        setups = self._parse_setups(str(raw_output))

        logger.info(f"[StonxCrew] Run complete — {len(setups)} setups returned.")
        return {
            "raw_output": str(raw_output),
            "setups": setups,
            "error": None,
        }

    @staticmethod
    def _parse_setups(raw: str) -> List[Dict]:
        """
        Extract JSON list from agent output.
        The agent is instructed to return pure JSON, but we handle markdown
        code blocks just in case.
        """
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # remove first and last fence lines
            inner = [l for l in lines if not l.startswith("```")]
            text = "\n".join(inner).strip()

        # Find the first '[' and last ']' to extract JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("[StonxCrew] Could not find JSON array in agent output")
            return []

        json_str = text[start : end + 1]
        try:
            data = json.loads(json_str)
            if isinstance(data, list):
                return data
            logger.warning("[StonxCrew] Parsed JSON is not a list")
            return []
        except json.JSONDecodeError as exc:
            logger.warning(f"[StonxCrew] JSON parse error: {exc}")
            return []
