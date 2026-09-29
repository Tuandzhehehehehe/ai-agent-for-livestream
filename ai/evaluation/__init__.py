"""Deterministic synthetic evaluation for the AI modules."""

__all__ = [
    "EvaluationDataError",
    "run_evaluation",
    "write_evaluation_reports",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ai.evaluation import runner

    return getattr(runner, name)