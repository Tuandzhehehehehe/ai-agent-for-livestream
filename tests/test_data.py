import json
import tempfile
import unittest
from pathlib import Path

from ai.data import (
    DataValidationError,
    get_effective_policies,
    get_faqs,
    get_product_by_id,
    load_faqs,
    load_policies,
    load_products,
    load_synthetic_data,
    search_records,
)


class SyntheticDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_synthetic_data()

    def test_loads_bundled_synthetic_records(self) -> None:
        self.assertEqual(len(self.dataset.products), 6)
        self.assertEqual(len(self.dataset.faqs), 8)
        self.assertEqual(len(self.dataset.policies), 4)

    def test_product_lookup_returns_match_or_none(self) -> None:
        product = get_product_by_id(self.dataset.products, "SYN-PROD-001")
        self.assertIsNotNone(product)
        self.assertEqual(product.name, "LumaNest Desk Lamp")
        self.assertIsNone(get_product_by_id(self.dataset.products, "not-a-product"))

    def test_faq_filters_by_category_and_product(self) -> None:
        by_category = get_faqs(self.dataset.faqs, category="PRODUCT_DETAILS")
        self.assertEqual(len(by_category), 4)
        by_product = get_faqs(self.dataset.faqs, product_id="SYN-PROD-001")
        self.assertEqual({faq.id for faq in by_product}, {"SYN-FAQ-001", "SYN-FAQ-002"})
        both = get_faqs(
            self.dataset.faqs,
            category="product_details",
            product_id="SYN-PROD-003",
        )
        self.assertEqual([faq.id for faq in both], ["SYN-FAQ-007"])

    def test_effective_policy_filter_excludes_inactive(self) -> None:
        effective = get_effective_policies(self.dataset.policies)
        self.assertEqual(len(effective), 3)
        self.assertTrue(all(policy.status == "effective" for policy in effective))
        self.assertNotIn("SYN-POLICY-WARRANTY-OLD", {p.id for p in effective})

    def test_search_covers_products_faqs_and_policy_content(self) -> None:
        product_results = search_records(
            "lumanest lamp",
            products=self.dataset.products,
            faqs=(),
            policies=(),
        )
        faq_results = search_records(
            "320 ml",
            products=(),
            faqs=self.dataset.faqs,
            policies=(),
        )
        policy_results = search_records(
            "fourteen simulated days",
            products=(),
            faqs=(),
            policies=self.dataset.policies,
        )
        self.assertEqual([item.record_id for item in product_results], ["SYN-PROD-001"])
        self.assertEqual([item.record_id for item in faq_results], ["SYN-FAQ-003"])
        self.assertEqual([item.record_id for item in policy_results], ["SYN-POLICY-RETURNS-001"])

    def test_empty_dataset_returns_empty_access_and_search_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for record_type, filename in (
                ("products", "products.json"),
                ("faqs", "faqs.json"),
                ("policies", "policies.json"),
            ):
                self._write_payload(directory / filename, record_type, [])
            empty = load_synthetic_data(directory)

        self.assertEqual(empty.products, ())
        self.assertEqual(empty.faqs, ())
        self.assertEqual(empty.policies, ())
        self.assertIsNone(get_product_by_id(empty.products, "missing"))
        self.assertEqual(get_faqs(empty.faqs, category="shipping"), ())
        self.assertEqual(get_effective_policies(empty.policies), ())
        self.assertEqual(
            search_records("anything", products=(), faqs=(), policies=()), ()
        )

    def test_missing_required_product_field_is_reported(self) -> None:
        record = self._valid_product()
        del record["name"]
        with self._temporary_records("products", [record]) as path:
            with self.assertRaisesRegex(DataValidationError, r"records\[0\]\.name"):
                load_products(path)

    def test_incorrect_product_field_type_is_reported(self) -> None:
        record = self._valid_product()
        record["price"] = "24.50"
        with self._temporary_records("products", [record]) as path:
            with self.assertRaisesRegex(DataValidationError, r"price must be a number"):
                load_products(path)

    def test_product_id_and_name_must_not_be_empty(self) -> None:
        for field in ("id", "name"):
            with self.subTest(field=field):
                record = self._valid_product()
                record[field] = "  "
                with self._temporary_records("products", [record]) as path:
                    with self.assertRaisesRegex(DataValidationError, "must not be empty"):
                        load_products(path)

    def test_non_finite_and_unrepresentable_prices_are_rejected(self) -> None:
        for price in (float("nan"), 10**400):
            with self.subTest(price_type=type(price).__name__):
                record = self._valid_product()
                record["price"] = price
                with self._temporary_records("products", [record]) as path:
                    with self.assertRaisesRegex(DataValidationError, "price must be finite"):
                        load_products(path)

    def test_negative_price_and_stock_are_rejected(self) -> None:
        for field, value, message in (
            ("price", -1, "price must be non-negative"),
            ("stock_quantity", -1, "stock_quantity must be non-negative"),
        ):
            with self.subTest(field=field):
                record = self._valid_product()
                record[field] = value
                with self._temporary_records("products", [record]) as path:
                    with self.assertRaisesRegex(DataValidationError, message):
                        load_products(path)

    def test_duplicate_ids_are_rejected(self) -> None:
        record = self._valid_product()
        with self._temporary_records("products", [record, record]) as path:
            with self.assertRaisesRegex(DataValidationError, "duplicates ID"):
                load_products(path)

    def test_unknown_faq_product_reference_is_rejected(self) -> None:
        faq = {
            "id": "SYN-FAQ-X",
            "question": "Question?",
            "answer": "Answer.",
            "category": "general",
            "product_ids": ["UNKNOWN-PRODUCT"],
        }
        with self._temporary_records("faqs", [faq]) as path:
            with self.assertRaisesRegex(
                DataValidationError, "references unknown product ID"
            ):
                load_faqs(path, known_product_ids={"SYN-PROD-001"})

    def test_policy_status_and_version_are_validated(self) -> None:
        invalid_policy = {
            "id": "SYN-POLICY-X",
            "category": "shipping",
            "content": "Synthetic example.",
            "status": "pending",
            "version": "1.0",
        }
        with self._temporary_records("policies", [invalid_policy]) as path:
            with self.assertRaisesRegex(DataValidationError, "status must be"):
                load_policies(path)

        invalid_policy["status"] = "effective"
        invalid_policy["version"] = "latest"
        with self._temporary_records("policies", [invalid_policy]) as path:
            with self.assertRaisesRegex(DataValidationError, "numeric dotted format"):
                load_policies(path)

    def test_malformed_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "products.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "Malformed JSON"):
                load_products(path)

    def test_missing_data_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "absent.json"
            with self.assertRaisesRegex(FileNotFoundError, "not found"):
                load_products(path)

    def test_custom_faq_file_requires_reference_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "faqs.json"
            with self.assertRaisesRegex(ValueError, "known_product_ids is required"):
                load_faqs(path)

    @staticmethod
    def _valid_product() -> dict[str, object]:
        return {
            "id": "SYN-PROD-X",
            "name": "Demo item",
            "description": "Synthetic demo product.",
            "price": 3.5,
            "stock_quantity": 2,
            "category": "demo",
            "attributes": {},
        }

    @staticmethod
    def _write_payload(path: Path, record_type: str, records: list[object]) -> None:
        path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "record_type": record_type,
                        "data_origin": "synthetic",
                        "dataset_label": "SYNTHETIC DEMO DATA",
                    },
                    "records": records,
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def _temporary_records(cls, record_type: str, records: list[object]):
        class TemporaryRecordFile:
            def __enter__(self):
                self.directory = tempfile.TemporaryDirectory()
                self.path = Path(self.directory.name) / f"{record_type}.json"
                cls._write_payload(self.path, record_type, records)
                return self.path

            def __exit__(self, exc_type, exc_value, traceback):
                self.directory.cleanup()

        return TemporaryRecordFile()


if __name__ == "__main__":
    unittest.main()