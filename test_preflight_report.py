"""Tests for the read-only monthly/period preflight report."""
import unittest
from datetime import date
from decimal import Decimal

from accounting_config import AccountingConfig, ProductRule, TipRule
from preflight_report import build_preflight_report, format_preflight_report
from zettle_client import ZettleAPIError


CONFIG = AccountingConfig(
    currency="EUR",
    product_rules=(
        ProductRule(frozenset({"Haarschnitt"}), "service", Decimal("19")),
        ProductRule(frozenset({"Snack"}), "food", Decimal("7")),
    ),
    category_environment={
        "service": "SERVICE_ID",
        "food": "FOOD_ID",
        "ownerTips": "OWNER_TIPS_ID",
    },
    tip_mode="byCashier",
    tip_rules=(
        TipRule("101", "entrepreneur", "ownerTips", Decimal("19")),
    ),
    refund_mode="separateCreditNote",
)


class FakeZettle:
    def __init__(self, by_date: dict):
        self.by_date = by_date

    def get_purchases_for_day(self, business_date: date):
        value = self.by_date.get(business_date, [])
        if isinstance(value, Exception):
            raise value
        return value


def _sale() -> dict:
    return {
        "currency": "EUR",
        "products": [
            {
                "name": "Haarschnitt",
                "unitPrice": 11900,
                "quantity": "1",
                "vatPercentage": 19,
            },
            {
                "name": "Snack",
                "unitPrice": 10700,
                "quantity": "1",
                "vatPercentage": 7,
            },
        ],
        "payments": [
            {"amount": 11900, "type": "CASH"},
            {"amount": 10700, "type": "IZETTLE_CARD"},
        ],
    }


def _refund() -> dict:
    return {
        "currency": "EUR",
        "amount": -10700,
        "refund": True,
        "refundsPurchaseUUID1": "original-purchase",
        "products": [{
            "name": "Snack",
            "unitPrice": 10700,
            "quantity": "-1",
            "grossValue": -10700,
            "vatPercentage": 7,
        }],
        "payments": [{
            "amount": -10700,
            "type": "IZETTLE_CARD",
            "references": {"refundsPayment": "original-payment"},
        }],
    }


class TestPreflightReport(unittest.TestCase):
    def test_mixed_rates_refunds_and_empty_days_are_summarized(self):
        days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
        report = build_preflight_report(
            days,
            FakeZettle({days[0]: [_sale()], days[1]: [_refund()]}),
            CONFIG,
        )
        self.assertEqual(report.valid_days, 2)
        self.assertEqual(report.empty_days, 1)
        self.assertEqual(report.product_sales_gross, Decimal("226.00"))
        self.assertEqual(report.summary.total_vat, Decimal("26.00"))
        self.assertEqual(report.summary.refund_total_gross, Decimal("107.00"))
        self.assertEqual(report.summary.refund_total_vat, Decimal("7.00"))
        self.assertEqual(report.summary.net_gross, Decimal("119.00"))
        self.assertFalse(report.blockers)

    def test_tips_are_shown_separately_by_role(self):
        business_date = date(2026, 9, 1)
        purchase = {
            "currency": "EUR",
            "userId": 101,
            "products": [{
                "name": "Haarschnitt",
                "unitPrice": 1000,
                "quantity": "1",
                "vatPercentage": 19,
            }],
            "payments": [{
                "amount": 1100,
                "gratuityAmount": 100,
                "type": "IZETTLE_CARD",
            }],
        }
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [purchase]}), CONFIG
        )
        self.assertEqual(report.tip_totals_by_role, {"entrepreneur": Decimal("1.00")})
        self.assertEqual(report.product_sales_gross, Decimal("10.00"))
        self.assertEqual(report.summary.total_gross, Decimal("11.00"))

    def test_unknown_product_blocks_and_day_is_excluded(self):
        business_date = date(2026, 9, 1)
        purchase = {
            "currency": "EUR",
            "products": [{
                "name": "Unbekannt",
                "unitPrice": 1000,
                "quantity": "1",
                "vatPercentage": 19,
            }],
            "payments": [{"amount": 1000, "type": "CASH"}],
        }
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [purchase]}), CONFIG
        )
        self.assertEqual(report.unmapped_products, {"Unbekannt"})
        self.assertIn(business_date, report.errors)
        self.assertEqual(report.summary.total_gross, Decimal("0"))
        self.assertTrue(report.blockers)

    def test_gift_card_is_a_blocker(self):
        business_date = date(2026, 9, 1)
        gift_card = {
            "currency": "EUR",
            "products": [{
                "name": "Geschenkkarte",
                "type": "GIFTCARD",
                "unitPrice": 5000,
                "quantity": "1",
            }],
            "payments": [{"amount": 5000, "type": "CASH"}],
        }
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [gift_card]}), CONFIG
        )
        self.assertEqual(report.gift_card_days, {business_date})
        self.assertTrue(report.blockers)

    def test_configured_zero_vat_product_still_requires_review(self):
        business_date = date(2026, 9, 1)
        config = AccountingConfig(
            currency="EUR",
            product_rules=(
                ProductRule(frozenset({"Steuerfrei"}), "exempt", Decimal("0")),
            ),
            category_environment={"exempt": "EXEMPT_ID"},
        )
        purchase = {
            "currency": "EUR",
            "products": [{
                "name": "Steuerfrei",
                "unitPrice": 1000,
                "quantity": "1",
                "vatPercentage": 0,
            }],
            "payments": [{"amount": 1000, "type": "CASH"}],
        }
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [purchase]}), config
        )
        self.assertEqual(report.zero_vat_products, {"Steuerfrei"})
        self.assertEqual(report.valid_days, 1)
        self.assertTrue(report.blockers)

    def test_rate_outside_zero_seven_nineteen_is_reported(self):
        business_date = date(2026, 9, 1)
        purchase = {
            "currency": "EUR",
            "products": [{
                "name": "Haarschnitt",
                "unitPrice": 11200,
                "quantity": "1",
                "vatPercentage": 12,
            }],
            "payments": [{"amount": 11200, "type": "CASH"}],
        }
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [purchase]}), CONFIG
        )
        self.assertEqual(report.unexpected_vat_rates, {Decimal("12")})
        self.assertIn(business_date, report.errors)
        self.assertTrue(report.blockers)

    def test_zettle_error_does_not_abort_remaining_days(self):
        first, second = date(2026, 9, 1), date(2026, 9, 2)
        report = build_preflight_report(
            [first, second],
            FakeZettle({first: ZettleAPIError("timeout"), second: [_sale()]}),
            CONFIG,
        )
        self.assertIn(first, report.errors)
        self.assertEqual(report.valid_days, 1)
        self.assertTrue(report.blockers)

    def test_report_contains_tax_categories_and_blocking_status(self):
        business_date = date(2026, 9, 1)
        report = build_preflight_report(
            [business_date], FakeZettle({business_date: [_sale()]}), CONFIG
        )
        text = format_preflight_report(report)
        self.assertIn("19%", text)
        self.assertIn("7%", text)
        self.assertIn("Nach Buchungskategorie", text)
        self.assertIn("KEINE TECHNISCHEN BLOCKER", text)
        self.assertIn("kein Zugriff auf Lexware", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
