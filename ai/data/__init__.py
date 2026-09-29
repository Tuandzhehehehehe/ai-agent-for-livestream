"""Validated synthetic data access for the AI package."""

from ai.data.access import get_effective_policies, get_faqs, get_product_by_id
from ai.data.loading import (
    DEFAULT_DATA_DIR,
    DataValidationError,
    load_faqs,
    load_policies,
    load_products,
    load_synthetic_data,
)
from ai.data.models import FAQ, Product, SalesPolicy, SearchResult, SyntheticDataset
from ai.data.search import search_records

__all__ = [
    "DEFAULT_DATA_DIR",
    "DataValidationError",
    "FAQ",
    "Product",
    "SalesPolicy",
    "SearchResult",
    "SyntheticDataset",
    "get_effective_policies",
    "get_faqs",
    "get_product_by_id",
    "load_faqs",
    "load_policies",
    "load_products",
    "load_synthetic_data",
    "search_records",
]