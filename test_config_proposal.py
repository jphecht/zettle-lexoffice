"""Tests for safe accounting configuration proposals."""
import json
import tempfile
import unittest
from pathlib import Path

from accounting_config import ConfigurationError, DEFAULT_CONFIG, load_accounting_config
from config_proposal import build_config_proposal


def _product(name: str, rate: object, product_type: str = "PRODUCT") -> dict:
    return {
        "name": name,
        "type": product_type,
        "variants": [{"vatPercentage": rate}],
    }


class TestConfigProposal(unittest.TestCase):
    def _load_proposal(self, proposal: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposal.json"
            path.write_text(json.dumps(proposal), encoding="utf-8")
            return load_accounting_config(str(path))

    def test_preserves_existing_rules_and_suggests_unknown_product(self):
        proposal, review = build_config_proposal(
            [
                _product("Dienstleistung", 19),
                _product("Neues Produkt", 7),
            ],
            DEFAULT_CONFIG,
        )
        self.assertEqual(proposal["proposalStatus"], "reviewRequired")
        suggestion = proposal["_review"]["suggestedMappings"][0]
        self.assertEqual(suggestion["product"], "Neues Produkt")
        self.assertEqual(suggestion["observedVatRate"], "7")
        self.assertIn(suggestion["placeholderCategory"], proposal["categories"])
        self.assertIn("Neues Produkt", review.unmapped_products)
        self.assertTrue(any(
            "Dienstleistung" in rule["names"] for rule in proposal["products"]
        ))

    def test_proposal_cannot_be_loaded_before_confirmation(self):
        proposal, _ = build_config_proposal(
            [_product("Dienstleistung", 19)], DEFAULT_CONFIG
        )
        with self.assertRaises(ConfigurationError):
            self._load_proposal(proposal)

    def test_confirmed_proposal_is_structurally_valid(self):
        proposal, _ = build_config_proposal(
            [_product("Dienstleistung", 19), _product("Neu", 7)],
            DEFAULT_CONFIG,
        )
        proposal["proposalStatus"] = "confirmed"
        config = self._load_proposal(proposal)
        self.assertEqual(config.rule_for_product("Neu").vat_rate, 7)

    def test_gift_cards_remain_excluded_and_listed(self):
        gift_card = {"name": "Geschenkkarte", "type": "GIFTCARD", "variants": [{}]}
        proposal, _ = build_config_proposal([gift_card], DEFAULT_CONFIG)
        self.assertEqual(
            proposal["_review"]["giftCardProducts"], ["Geschenkkarte"]
        )
        self.assertFalse(any(
            "Geschenkkarte" in rule["names"] for rule in proposal["products"]
        ))

    def test_output_is_deterministic(self):
        products = [_product("B", 7), _product("A", 19)]
        first, _ = build_config_proposal(products, DEFAULT_CONFIG)
        second, _ = build_config_proposal(list(reversed(products)), DEFAULT_CONFIG)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
