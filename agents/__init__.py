"""Agents package.

Uses lazy attribute access (PEP 562) so that importing a specific agent
module (e.g. ``agents.research_agent``) does NOT eagerly import the CrewAI /
LangChain based ``scanner_agent``. This keeps deterministic / mockable agents
importable and unit-testable without the heavy optional LLM dependencies
installed.
"""

__all__ = ["ScannerAgent"]

_LAZY = {"ScannerAgent": "agents.scanner_agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        return getattr(module, name)
    raise AttributeError(f"module 'agents' has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
