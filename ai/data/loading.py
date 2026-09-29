"""Load and validate explicitly synthetic JSON records."""

import json
import math
import re
from pathlib import Path
from typing import Iterable, Mapping

from ai.data.models import FAQ, Product, SalesPolicy, SyntheticDataset

DEFAULT_DATA_DIR = Path(__file__).parent / "synthetic"
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")


class DataValidationError(ValueError):
    """Raised when a synthetic data file is malformed or fails validation."""


def load_products(path: str | Path | None = None) -> tuple[Product, ...]:
    """Load products from a synthetic data file and validate every record."""
    source = Path(path) if path is not None else DEFAULT_DATA_DIR / "products.json"
    records = _read_records(source, "products")
    products: list[Product] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        location = f"{source} records[{index}]"
        _require_object(record, location)
        product_id = _required_string(record, "id", location)
        _check_unique_id(product_id, seen_ids, location)
        name = _required_string(record, "name", location)
        description = _required_string(record, "description", location)
        price = _required_number(record, "price", location)
        if price < 0:
            raise DataValidationError(f"{location}.price must be non-negative")
        stock = _required_integer(record, "stock_quantity", location)
        if stock < 0:
            raise DataValidationError(
                f"{location}.stock_quantity must be non-negative"
            )
        category = _required_string(record, "category", location)
        attributes = _attributes(record, location)
        products.append(
            Product(
                id=product_id,
                name=name,
                description=description,
                price=price,
                stock_quantity=stock,
                category=category,
                attributes=attributes,
            )
        )

    return tuple(products)


def load_faqs(
    path: str | Path | None = None,
    *,
    known_product_ids: Iterable[str] | None = None,
) -> tuple[FAQ, ...]:
    """Load FAQs and validate product references against known product IDs.

    When loading the bundled FAQ file, product IDs are loaded from the bundled
    catalog by default. A custom FAQ file requires explicit known product IDs.
    """
    source = Path(path) if path is not None else DEFAULT_DATA_DIR / "faqs.json"
    if known_product_ids is None:
        if path is not None:
            raise ValueError(
                "known_product_ids is required when loading a custom FAQ file"
            )
        known_ids = {product.id for product in load_products()}
    else:
        known_ids = set(known_product_ids)

    records = _read_records(source, "faqs")
    faqs: list[FAQ] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        location = f"{source} records[{index}]"
        _require_object(record, location)
        faq_id = _required_string(record, "id", location)
        _check_unique_id(faq_id, seen_ids, location)
        question = _required_string(record, "question", location)
        answer = _required_string(record, "answer", location)
        category = _required_string(record, "category", location)
        raw_product_ids = record.get("product_ids")
        if not isinstance(raw_product_ids, list):
            raise DataValidationError(f"{location}.product_ids must be a list")

        product_ids: list[str] = []
        for product_index, product_id in enumerate(raw_product_ids):
            reference_location = f"{location}.product_ids[{product_index}]"
            if not isinstance(product_id, str) or not product_id.strip():
                raise DataValidationError(
                    f"{reference_location} must be a non-empty string"
                )
            if product_id in product_ids:
                raise DataValidationError(
                    f"{reference_location} duplicates product ID {product_id!r}"
                )
            if product_id not in known_ids:
                raise DataValidationError(
                    f"{reference_location} references unknown product ID "
                    f"{product_id!r}"
                )
            product_ids.append(product_id)

        faqs.append(
            FAQ(
                id=faq_id,
                question=question,
                answer=answer,
                category=category,
                product_ids=tuple(product_ids),
            )
        )

    return tuple(faqs)


def load_policies(path: str | Path | None = None) -> tuple[SalesPolicy, ...]:
    """Load policies and validate IDs, statuses, and numeric dotted versions."""
    source = Path(path) if path is not None else DEFAULT_DATA_DIR / "policies.json"
    records = _read_records(source, "policies")
    policies: list[SalesPolicy] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        location = f"{source} records[{index}]"
        _require_object(record, location)
        policy_id = _required_string(record, "id", location)
        _check_unique_id(policy_id, seen_ids, location)
        category = _required_string(record, "category", location)
        content = _required_string(record, "content", location)
        status = _required_string(record, "status", location)
        if status not in {"effective", "inactive"}:
            raise DataValidationError(
                f"{location}.status must be 'effective' or 'inactive'"
            )
        version = _required_string(record, "version", location)
        if not _VERSION_PATTERN.fullmatch(version):
            raise DataValidationError(
                f"{location}.version must use a numeric dotted format, "
                "for example '1.0' or '1.0.0'"
            )
        policies.append(
            SalesPolicy(
                id=policy_id,
                category=category,
                content=content,
                status=status,
                version=version,
            )
        )

    return tuple(policies)


def load_synthetic_data(data_dir: str | Path | None = None) -> SyntheticDataset:
    """Load all bundled (or supplied-directory) data and validate references."""
    directory = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    products = load_products(directory / "products.json")
    faqs = load_faqs(
        directory / "faqs.json",
        known_product_ids=(product.id for product in products),
    )
    policies = load_policies(directory / "policies.json")
    return SyntheticDataset(products=products, faqs=faqs, policies=policies)


def _read_records(path: Path, expected_type: str) -> list[object]:
    if not path.is_file():
        raise FileNotFoundError(f"Synthetic data file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DataValidationError(
            f"Malformed JSON in {path} at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    except UnicodeDecodeError as error:
        raise DataValidationError(f"File {path} is not valid UTF-8: {error}") from error

    if not isinstance(payload, dict):
        raise DataValidationError(f"{path} must contain a JSON object")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise DataValidationError(f"{path} metadata must be a JSON object")
    if metadata.get("record_type") != expected_type:
        raise DataValidationError(
            f"{path} metadata.record_type must be {expected_type!r}"
        )
    if metadata.get("data_origin") != "synthetic":
        raise DataValidationError(
            f"{path} metadata.data_origin must be 'synthetic'"
        )
    label = metadata.get("dataset_label")
    if not isinstance(label, str) or not {"synthetic", "demo"}.issubset(
        set(re.findall(r"[a-z]+", label.casefold()))
    ):
        raise DataValidationError(
            f"{path} metadata.dataset_label must identify synthetic demo data"
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise DataValidationError(f"{path} records must be a JSON array")
    return records


def _require_object(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise DataValidationError(f"{location} must be a JSON object")
    return value


def _required_string(
    record: Mapping[str, object], field: str, location: str
) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise DataValidationError(f"{location}.{field} must be a string")
    if not value.strip():
        raise DataValidationError(f"{location}.{field} must not be empty")
    return value.strip()


def _required_number(record: Mapping[str, object], field: str, location: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataValidationError(f"{location}.{field} must be a number")
    try:
        number = float(value)
    except OverflowError as error:
        raise DataValidationError(f"{location}.{field} must be finite") from error
    if not math.isfinite(number):
        raise DataValidationError(f"{location}.{field} must be finite")
    return number


def _required_integer(
    record: Mapping[str, object], field: str, location: str
) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataValidationError(f"{location}.{field} must be an integer")
    return value


def _attributes(record: Mapping[str, object], location: str) -> dict[str, str | int | float | bool]:
    value = record.get("attributes")
    if not isinstance(value, dict):
        raise DataValidationError(f"{location}.attributes must be a JSON object")
    validated: dict[str, str | int | float | bool] = {}
    for key, attribute in value.items():
        if not isinstance(key, str) or not key.strip():
            raise DataValidationError(
                f"{location}.attributes keys must be non-empty strings"
            )
        if not isinstance(attribute, (str, int, float, bool)):
            raise DataValidationError(
                f"{location}.attributes[{key!r}] must be a JSON scalar"
            )
        if isinstance(attribute, float) and not math.isfinite(attribute):
            raise DataValidationError(
                f"{location}.attributes[{key!r}] must be finite"
            )
        validated[key.strip()] = attribute
    return validated


def _check_unique_id(record_id: str, seen_ids: set[str], location: str) -> None:
    if record_id in seen_ids:
        raise DataValidationError(f"{location}.id duplicates ID {record_id!r}")
    seen_ids.add(record_id)