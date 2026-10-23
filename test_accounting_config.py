"""Tests for the user-editable accounting configuration."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from accounting_config import (
    ConfigurationError,
    DEFAULT_CONFIG,
    load_accounting_config,
)


def _valid_config() -> dict:
    return {
        "currency": "EUR",
        "categories": {
            "service": {"environmentVariable": "SERVICE_ID"},
            "food": {"environmentVariable": "FOOD_ID"},
            "ownerTips": {"environmentVariable": "OWNER_TIPS_ID"},
            "employeeTips": {"environmentVariable": "EMPLOYEE_TIPS_ID"},
        },
        "products": [
            {"names": ["Haarschnitt"], "category": "service", "vatRate": "19"},
            {"names": ["Snack"], "category": "food", "vatRate": "7"},
        ],
        "adjustments": {
            "discounts": "useGrossValue",
            "tips": "reject",
            "refunds": "reject",
        },
    }


class TestAccountingConfig(unittest.TestCase):
    def _load(self, data: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "accounting.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_accounting_config(str(path))

    def test_default_preserves_existing_mapping(self):
        self.assertEqual(load_accounting_config(None), DEFAULT_CONFIG)
        self.assertEqual(
            DEFAULT_CONFIG.rule_for_product("Dienstleistung").category, "service"
        )

    def test_example_configuration_is_valid(self):
        example = Path(__file__).with_name("accounting.example.json")
        config = load_accounting_config(str(example))
        self.assertEqual(config.tip_mode, "byCashier")
        self.assertEqual(config.tip_rule_for_user(1002).role, "employee")

    def test_loads_multiple_categories_and_rates(self):
        config = self._load(_valid_config())
        self.assertEqual(config.rule_for_product("Snack").category, "food")
        self.assertEqual(config.rule_for_product("Snack").vat_rate, Decimal("7"))
        self.assertTrue(config.allow_discounts)

    def test_category_ids_are_read_from_environment(self):
        config = self._load(_valid_config())
        self.assertEqual(
            config.category_ids({
                "SERVICE_ID": "a",
                "FOOD_ID": "b",
                "OWNER_TIPS_ID": "c",
                "EMPLOYEE_TIPS_ID": "d",
            }),
            {
                "service": "a",
                "food": "b",
                "ownerTips": "c",
                "employeeTips": "d",
            },
        )

    def test_missing_category_environment_value_is_rejected(self):
        config = self._load(_valid_config())
        with self.assertRaises(ConfigurationError):
            config.category_ids({"SERVICE_ID": "a"})

    def test_duplicate_product_name_is_rejected(self):
        data = _valid_config()
        data["products"][1]["names"] = ["Haarschnitt"]
        with self.assertRaises(ConfigurationError):
            self._load(data)

    def test_unknown_category_is_rejected(self):
        data = _valid_config()
        data["products"][0]["category"] = "missing"
        with self.assertRaises(ConfigurationError):
            self._load(data)

    def test_unsupported_adjustment_mode_is_rejected(self):
        data = _valid_config()
        data["adjustments"]["tips"] = "guess"
        with self.assertRaises(ConfigurationError):
            self._load(data)

    def test_separate_refund_credit_notes_can_be_enabled(self):
        data = _valid_config()
        data["adjustments"]["refunds"] = "separateCreditNote"
        config = self._load(data)
        self.assertEqual(config.refund_mode, "separateCreditNote")

    def test_unknown_refund_mode_is_rejected(self):
        data = _valid_config()
        data["adjustments"]["refunds"] = "netWithSales"
        with self.assertRaises(ConfigurationError):
            self._load(data)

    def test_loads_cashier_tip_rules(self):
        data = _valid_config()
        data["adjustments"]["tips"] = {
            "mode": "byCashier",
            "cashiers": [
                {
                    "userId": 101,
                    "displayName": "Inhaber",
                    "role": "entrepreneur",
                    "category": "ownerTips",
                    "vatRate": "19",
                },
                {
                    "userId": "202",
                    "role": "employee",
                    "category": "employeeTips",
                    "vatRate": "0",
                },
            ],
        }
        config = self._load(data)
        self.assertEqual(config.tip_mode, "byCashier")
        self.assertEqual(config.tip_rule_for_user(101).role, "entrepreneur")
        self.assertEqual(config.tip_rule_for_user("202").vat_rate, Decimal("0"))

    def test_employee_tip_with_vat_is_rejected(self):
        data = _valid_config()
        data["adjustments"]["tips"] = {
            "mode": "byCashier",
            "cashiers": [{
                "userId": 202,
                "role": "employee",
                "category": "employeeTips",
                "vatRate": "19",
            }],
        }
        with self.assertRaises(ConfigurationError):
            self._load(data)

    def test_duplicate_cashier_id_is_rejected(self):
        data = _valid_config()
        rule = {
            "userId": 202,
            "role": "employee",
            "category": "employeeTips",
            "vatRate": "0",
        }
        data["adjustments"]["tips"] = {
            "mode": "byCashier",
            "cashiers": [rule, dict(rule)],
        }
        with self.assertRaises(ConfigurationError):
            self._load(data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
