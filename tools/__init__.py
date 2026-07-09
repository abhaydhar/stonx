"""Tools package — LangChain/CrewAI tool wrappers.

Uses lazy attribute access (PEP 562) so importing a lightweight tool module
(e.g. ``tools.web_tools``) does not eagerly import the LangChain-decorated
data/analysis tools. This keeps deterministic / mockable tooling importable
without ``langchain`` installed.
"""

__all__ = [
    "fetch_ohlcv_tool",
    "fetch_universe_tool",
    "screen_fundamentals_tool",
    "detect_patterns_tool",
    "calculate_volume_profile_tool",
    "validate_risk_reward_tool",
    "check_market_regime_tool",
]

_LAZY = {
    "fetch_ohlcv_tool": "tools.data_tools",
    "fetch_universe_tool": "tools.data_tools",
    "screen_fundamentals_tool": "tools.data_tools",
    "detect_patterns_tool": "tools.analysis_tools",
    "calculate_volume_profile_tool": "tools.analysis_tools",
    "validate_risk_reward_tool": "tools.analysis_tools",
    "check_market_regime_tool": "tools.analysis_tools",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        return getattr(module, name)
    raise AttributeError(f"module 'tools' has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
