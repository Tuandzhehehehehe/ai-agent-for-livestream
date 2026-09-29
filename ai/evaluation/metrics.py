"""Small metric helpers with explicit numerators and denominators."""

from typing import Any


def ratio_metric(
    numerator: int,
    denominator: int,
    *,
    definition: str,
) -> dict[str, Any]:
    """Return a reproducible ratio; undefined empty denominators are ``None``."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "definition": definition,
        "target": None,
    }