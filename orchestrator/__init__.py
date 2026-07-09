"""Orchestrator package.

Lazy attribute access (PEP 562) keeps the deterministic
``orchestrator.pipeline`` importable without pulling in the CrewAI-based
``orchestrator.crew`` (and therefore without requiring ``crewai`` installed).
"""

__all__ = ["StonxCrew", "ScanResearchRiskPipeline", "PipelineResult"]

_LAZY = {
    "StonxCrew": "orchestrator.crew",
    "ScanResearchRiskPipeline": "orchestrator.pipeline",
    "PipelineResult": "orchestrator.pipeline",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        return getattr(module, name)
    raise AttributeError(f"module 'orchestrator' has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
