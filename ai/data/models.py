"""Typed records used by the synthetic data layer."""

from dataclasses import dataclass, field
from typing import Literal, Mapping

JSONScalar = str | int | float | bool
PolicyStatus = Literal["effective", "inactive"]
SearchRecordType = Literal["product", "faq", "policy"]


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    description: str
    price: float
    stock_quantity: int
    category: str
    attributes: Mapping[str, JSONScalar] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FAQ:
    id: str
    question: str
    answer: str
    category: str
    product_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SalesPolicy:
    id: str
    category: str
    content: str
    status: PolicyStatus
    version: str


@dataclass(frozen=True, slots=True)
class SyntheticDataset:
    products: tuple[Product, ...]
    faqs: tuple[FAQ, ...]
    policies: tuple[SalesPolicy, ...]


@dataclass(frozen=True, slots=True)
class SearchResult:
    record_type: SearchRecordType
    record_id: str
    title: str
    snippet: str