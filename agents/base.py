"""
Base Agent class — shared configuration and LLM initialisation for all agents.
"""

import logging
import os
from typing import List

from langchain_anthropic import ChatAnthropic
from langchain.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Thin base class that every StockScanner agent inherits from.
    Provides:
      - Consistent LLM initialisation via ChatAnthropic
      - Tool list management
      - Logging helpers
    """

    def __init__(
        self,
        model: str,
        tools: List[BaseTool],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Create a .env file with ANTHROPIC_API_KEY=sk-ant-..."
            )

        self.llm = ChatAnthropic(
            model=model,
            anthropic_api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.tools = tools
        self.model = model
        logger.info(f"[{self.__class__.__name__}] Initialised with model={model}")

    @property
    def tool_names(self) -> List[str]:
        return [t.name for t in self.tools]
