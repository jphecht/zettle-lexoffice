"""Tests for the read-only Zettle product catalog review."""
import unittest
from dataclasses import replace
from decimal import Decimal

from accounting_config import DEFAULT_CONFIG, GiftCardPolicy
from catalog_review import format_catalog_review, review_product_catalog


def _product(name: str, *rates: object, product_type: str = "PRODUCT") -> dict:
    return {
        "name": name,
        "type": product_type,
        "variants": [{"vatPercentage": rate} for rate in rates],
    }


class TestCatalogReview(unittest.TestCase):
    def test_matching_catalog_is_ready(self):
        review = review_product_catalog(
            [
                _product("Dienstleistung", "19"),
                _product("Damen WaSchFö", 19),
                _product("Verkauf", "19.0"),
            ],
            DEFAULT_CONFIG,
        )
        self.assertFalse(review.blockers)
        self.assertEqual(review.vat_rates, {Decimal("19")})
        self.assertEqual(review.product_count, 3)
        self.assertEqual(review.variant_count, 3)

    def test_unknown_product_is_blocker(self):
        review = review_product_catalog(
            [_product("Unbekannt", 7)], DEFAULT_CONFIG
        )
        self.assertTrue(review.blockers)
        self.assertEqual(review.unmapped_products, {"Unbekannt"})

    def test_rate_change_is_blocker(self):
        review = review_product_catalog(
            [_product("Dienstleistung", 7)], DEFAULT_CONFIG
        )
        self.assertEqual(review.rate_conflicts, {"Dienstleistung"})

    def test_mixed_variant_rates_are_blocker(self):
        review = review_product_catalog(
            [_product("Verkauf", 7, 19)], DEFAULT_CONFIG
        )
        self.assertEqual(review.rate_conflicts, {"Verkauf"})

    def test_zero_rate_requires_manual_review(self):
        config = replace(
            DEFAULT_CONFIG,
            product_rules=(
                replace(DEFAULT_CONFIG.product_rules[0], vat_rate=Decimal("0")),
            ),
        )
        review = review_product_catalog(
            [_product("Dienstleistung", 0), _product("Damen WaSchFö", 0)], config
        )
        self.assertTrue(review.blockers)
        self.assertEqual(
            review.zero_rate_products, {"Dienstleistung", "Damen WaSchFö"}
        )

    def test_tax_exempt_variant_without_percentage_is_treated_as_zero(self):
        config = replace(
            DEFAULT_CONFIG,
            product_rules=(
                replace(DEFAULT_CONFIG.product_rules[1], vat_rate=Decimal("0")),
            ),
        )
        product = {
            "name": "Verkauf",
            "variants": [{"vatPercentage": None, "taxExempt": True}],
        }
        review = review_product_catalog([product], config)
        self.assertEqual(review.zero_rate_products, {"Verkauf"})
        self.assertNotIn("Verkauf", review.invalid_products)
        self.assertTrue(review.blockers)

    def test_gift_card_is_reported_without_needing_vat(self):
        gift_card = {"name": "Geschenkkarte", "type": "GIFTCARD", "variants": [{}]}
        review = review_product_catalog([gift_card], DEFAULT_CONFIG)
        self.assertEqual(review.gift_card_products, {"Geschenkkarte"})
        self.assertNotIn("Geschenkkarte", review.invalid_products)
        self.assertTrue(review.blockers)

    def test_invalid_variant_is_reported(self):
        review = review_product_catalog(
            [{"name": "Verkauf", "variants": [{"vatPercentage": "NaN"}]}],
            DEFAULT_CONFIG,
        )
        self.assertEqual(review.invalid_products, {"Verkauf"})

    def test_report_contains_voucher_candidate_and_status(self):
        policy = GiftCardPolicy(
            mode="reviewRequired",
            industry_profile="hairAndBeauty",
            redeemable_categories=frozenset({"service"}),
            vat_rates=(Decimal("19"),),
        )
        config = replace(DEFAULT_CONFIG, gift_cards=policy)
        review = review_product_catalog([_product("Dienstleistung", 19)], config)
        report = format_catalog_review(review, config)
        self.assertIn("Einzweckgutschein-Kandidat", report)
        self.assertIn("hairAndBeauty", report)
        self.assertIn("Konfiguriert, aber nicht im Katalog", report)
        self.assertIn("ohne Blocker", report)

    def test_catalog_rate_change_changes_voucher_candidate(self):
        policy = GiftCardPolicy(
            mode="reviewRequired",
            industry_profile="retail",
            redeemable_categories=frozenset({"service", "sales"}),
            vat_rates=(Decimal("19"),),
        )
        config = replace(DEFAULT_CONFIG, gift_cards=policy)
        review = review_product_catalog(
            [
                _product("Dienstleistung", 19),
                _product("Verkauf", 7),
            ],
            config,
        )
        self.assertEqual(
            review.gift_card_recommendation, "multiPurposeCandidate"
        )
        self.assertEqual(review.redeemable_vat_rates, {Decimal("7"), Decimal("19")})
        self.assertTrue(review.blockers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
