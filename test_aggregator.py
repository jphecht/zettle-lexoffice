"""Unit tests for the aggregator module."""
import unittest
from decimal import Decimal

from accounting_config import AccountingConfig, GiftCardPolicy, ProductRule, TipRule
from aggregator import (
    AggregationError,
    DailySummary,
    SERVICE_PRODUCT_NAMES,
    aggregate,
    _product_gross,
    _vat_from_gross,
)


def _purchase(service_cents=0, sales_cents=0, cash_cents=0, card_cents=0) -> dict:
    products = []
    if service_cents:
        products.append({"name": "Dienstleistung", "unitPrice": service_cents, "quantity": "1"})
    if sales_cents:
        products.append({"name": "Verkauf", "unitPrice": sales_cents, "quantity": "1"})
    total = (service_cents + sales_cents)
    if cash_cents == 0 and card_cents == 0:
        cash_cents = total  # default: all cash
    payments = []
    if cash_cents:
        payments.append({"amount": cash_cents, "type": "CASH"})
    if card_cents:
        payments.append({"amount": card_cents, "type": "IZETTLE_CARD"})
    return {"currency": "EUR", "products": products, "payments": payments}


def _custom_config(
    *, allow_discounts=False, tips=False, refunds=False, gift_cards=None
) -> AccountingConfig:
    return AccountingConfig(
        currency="EUR",
        product_rules=(
            ProductRule(frozenset({"Haarschnitt"}), "hair", Decimal("19")),
            ProductRule(frozenset({"Snack"}), "food", Decimal("7")),
        ),
        category_environment={
            "hair": "HAIR_ID",
            "food": "FOOD_ID",
            "ownerTips": "OWNER_TIPS_ID",
            "employeeTips": "EMPLOYEE_TIPS_ID",
        },
        allow_discounts=allow_discounts,
        tip_mode="byCashier" if tips else "reject",
        tip_rules=(
            TipRule("101", "entrepreneur", "ownerTips", Decimal("19")),
            TipRule("202", "employee", "employeeTips", Decimal("0")),
        ) if tips else (),
        refund_mode="separateCreditNote" if refunds else "reject",
        gift_cards=gift_cards or GiftCardPolicy(),
    )


def _refund(
    name="Haarschnitt", amount_cents=1000, payment_type="IZETTLE_CARD"
) -> dict:
    return {
        "currency": "EUR",
        "amount": -amount_cents,
        "refund": True,
        "refundsPurchaseUUID1": "original-purchase-uuid",
        "products": [{
            "name": name,
            "unitPrice": amount_cents,
            "quantity": "-1",
            "grossValue": -amount_cents,
            "vatPercentage": 19,
        }],
        "payments": [{
            "amount": -amount_cents,
            "type": payment_type,
            "references": {"refundsPayment": "original-payment-uuid"},
        }],
    }


class TestVatCalculation(unittest.TestCase):
    def test_vat_from_119(self):
        self.assertEqual(_vat_from_gross(Decimal("119.00")), Decimal("19.00"))

    def test_vat_from_100(self):
        self.assertEqual(_vat_from_gross(Decimal("100.00")), Decimal("15.97"))

    def test_refund(self):
        self.assertEqual(_vat_from_gross(Decimal("-119.00")), Decimal("-19.00"))


class TestProductGross(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_product_gross({"unitPrice": 3800, "quantity": "1"}), Decimal("38.00"))

    def test_qty_2(self):
        self.assertEqual(_product_gross({"unitPrice": 1000, "quantity": "2"}), Decimal("20.00"))

    def test_invalid(self):
        with self.assertRaises(AggregationError):
            _product_gross({"unitPrice": "bad"})

    def test_refund_is_returned_as_positive_magnitude(self):
        self.assertEqual(
            _product_gross(
                {"unitPrice": 500, "quantity": "-1", "grossValue": -500},
                refund=True,
            ),
            Decimal("5.00"),
        )


class TestAggregate(unittest.TestCase):
    def test_empty(self):
        self.assertTrue(aggregate([]).is_empty())

    def test_pure_cash_service(self):
        s = aggregate([_purchase(service_cents=11900, cash_cents=11900)])
        self.assertEqual(s.cash_service_gross, Decimal("119.00"))
        self.assertEqual(s.cash_service_vat,   Decimal("19.00"))
        self.assertEqual(s.card_service_gross, Decimal("0"))
        self.assertTrue(s.has_cash)
        self.assertFalse(s.has_card)

    def test_real_zettle_cash_payment_type(self):
        purchase = _purchase(service_cents=11900, cash_cents=11900)
        purchase["payments"][0]["type"] = "IZETTLE_CASH"
        summary = aggregate([purchase])
        self.assertEqual(summary.cash_service_gross, Decimal("119.00"))
        self.assertFalse(summary.has_card)

    def test_pure_card_sales(self):
        s = aggregate([_purchase(sales_cents=5950, card_cents=5950)])
        self.assertEqual(s.card_sales_gross, Decimal("59.50"))
        self.assertEqual(s.cash_sales_gross, Decimal("0"))
        self.assertTrue(s.has_card)
        self.assertFalse(s.has_cash)

    def test_pure_cash_mixed_products(self):
        s = aggregate([_purchase(service_cents=3800, sales_cents=3400, cash_cents=7200)])
        self.assertEqual(s.cash_service_gross, Decimal("38.00"))
        self.assertEqual(s.cash_sales_gross,   Decimal("34.00"))
        self.assertEqual(s.card_service_gross, Decimal("0"))
        self.assertEqual(s.total_gross,        Decimal("72.00"))

    def test_synthetic_day_with_multiple_cash_sales(self):
        # Rein synthetisches Beispiel: vier Käufe, alles bar.
        purchases = [
            _purchase(service_cents=4100, sales_cents=2300, cash_cents=6400),
            _purchase(service_cents=2700, cash_cents=2700),
            _purchase(sales_cents=3100, cash_cents=3100),
            _purchase(service_cents=4600, cash_cents=4600),
        ]
        s = aggregate(purchases)
        self.assertEqual(s.service_gross, Decimal("114.00"))
        self.assertEqual(s.sales_gross,   Decimal("54.00"))
        self.assertEqual(s.total_gross,   Decimal("168.00"))
        self.assertFalse(s.has_card)

    def test_mixed_payment_proportional_split(self):
        # 100€ purchase: 50€ cash + 50€ card, 60€ service + 40€ sales
        s = aggregate([_purchase(service_cents=6000, sales_cents=4000,
                                  cash_cents=5000, card_cents=5000)])
        # Each product splits 50/50
        self.assertAlmostEqual(float(s.cash_service_gross), 30.0, places=1)
        self.assertAlmostEqual(float(s.card_service_gross), 30.0, places=1)
        self.assertAlmostEqual(float(s.cash_sales_gross),   20.0, places=1)
        self.assertAlmostEqual(float(s.card_sales_gross),   20.0, places=1)
        self.assertAlmostEqual(float(s.total_gross),       100.0, places=1)

    def test_unknown_product_is_rejected(self):
        p = {"products": [{"name": "Sonstiges", "unitPrice": 5000, "quantity": "1"}],
             "payments": [{"amount": 5000, "type": "CASH"}], "currency": "EUR"}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_non_dict_product_is_rejected(self):
        p = {"products": [True, None, {"name": "Dienstleistung", "unitPrice": 1000, "quantity": "1"}],
             "payments": [{"amount": 1000, "type": "CASH"}], "currency": "EUR"}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_unknown_payment_type_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1000)
        p["payments"][0]["type"] = "MYSTERY"
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_non_eur_purchase_is_rejected(self):
        p = _purchase(service_cents=1000)
        p["currency"] = "GBP"
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_missing_currency_is_rejected(self):
        p = _purchase(service_cents=1000)
        del p["currency"]
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_refund_flag_is_rejected(self):
        p = _purchase(service_cents=1000)
        p["refund"] = True
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_refund_becomes_separate_positive_summary(self):
        summary = aggregate([_refund()], _custom_config(refunds=True))
        self.assertEqual(summary.total_gross, Decimal("0"))
        self.assertEqual(summary.refund_total_gross, Decimal("10.00"))
        self.assertEqual(summary.net_gross, Decimal("-10.00"))
        self.assertEqual(summary.refund_card_gross, Decimal("10.00"))
        self.assertTrue(summary.has_card_refund)
        self.assertFalse(summary.has_card)

    def test_sale_and_refund_are_not_netted_before_booking(self):
        sale = {
            "currency": "EUR",
            "amount": 2000,
            "products": [{
                "name": "Haarschnitt", "unitPrice": 2000, "quantity": "1",
            }],
            "payments": [{"amount": 2000, "type": "IZETTLE_CARD"}],
        }
        summary = aggregate([sale, _refund()], _custom_config(refunds=True))
        self.assertEqual(summary.card_gross, Decimal("20.00"))
        self.assertEqual(summary.refund_card_gross, Decimal("10.00"))
        self.assertEqual(summary.net_gross, Decimal("10.00"))

    def test_partial_refund_uses_refunded_amount(self):
        summary = aggregate([_refund(amount_cents=500)], _custom_config(refunds=True))
        self.assertEqual(summary.refund_total_gross, Decimal("5.00"))

    def test_refund_preserves_multiple_categories_rates_and_payments(self):
        refund = {
            "currency": "EUR",
            "amount": -22600,
            "refund": True,
            "refundsPurchaseUUID1": "original-purchase-uuid",
            "products": [
                {
                    "name": "Haarschnitt", "unitPrice": 11900,
                    "quantity": "-1", "grossValue": -11900,
                    "vatPercentage": 19,
                },
                {
                    "name": "Snack", "unitPrice": 10700,
                    "quantity": "-1", "grossValue": -10700,
                    "vatPercentage": 7,
                },
            ],
            "payments": [
                {
                    "amount": -10000,
                    "type": "CASH",
                    "references": {"refundsPayment": "cash-payment"},
                },
                {
                    "amount": -12600,
                    "type": "IZETTLE_CARD",
                    "references": {"refundsPayment": "card-payment"},
                },
            ],
        }
        summary = aggregate([refund], _custom_config(refunds=True))
        self.assertEqual(summary.refund_total_gross, Decimal("226.00"))
        self.assertEqual(summary.refund_cash_gross, Decimal("100.00"))
        self.assertEqual(summary.refund_card_gross, Decimal("126.00"))
        self.assertEqual(
            summary.refund_category_totals(),
            {"hair": Decimal("119.00"), "food": Decimal("107.00")},
        )

    def test_refund_with_positive_payment_is_rejected(self):
        refund = _refund()
        refund["payments"][0]["amount"] = 1000
        refund["amount"] = 1000
        with self.assertRaises(AggregationError):
            aggregate([refund], _custom_config(refunds=True))

    def test_refund_without_original_purchase_reference_is_rejected(self):
        refund = _refund()
        del refund["refundsPurchaseUUID1"]
        with self.assertRaises(AggregationError):
            aggregate([refund], _custom_config(refunds=True))

    def test_refund_without_original_payment_reference_is_rejected(self):
        refund = _refund()
        del refund["payments"][0]["references"]
        with self.assertRaises(AggregationError):
            aggregate([refund], _custom_config(refunds=True))

    def test_refunded_tip_is_rejected_explicitly(self):
        refund = _refund()
        refund["payments"][0]["gratuityAmount"] = -100
        with self.assertRaises(AggregationError):
            aggregate([refund], _custom_config(refunds=True, tips=True))

    def test_explicit_non_19_percent_vat_is_rejected(self):
        p = _purchase(service_cents=1000)
        p["products"][0]["vatPercentage"] = 7
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_explicit_19_percent_vat_is_accepted(self):
        p = _purchase(service_cents=11900)
        p["products"][0]["vatPercentage"] = 19.0
        self.assertEqual(aggregate([p]).total_gross, Decimal("119.00"))

    def test_discounted_gross_value_is_rejected(self):
        p = _purchase(service_cents=1000)
        p["products"][0]["grossValue"] = 900
        p["payments"][0]["amount"] = 900
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_configured_discount_uses_reported_gross_value(self):
        purchase = {
            "currency": "EUR",
            "products": [{
                "name": "Haarschnitt", "unitPrice": 1000, "quantity": "1",
                "grossValue": 900, "vatPercentage": 19,
            }],
            "payments": [{"amount": 900, "type": "CASH"}],
        }
        summary = aggregate([purchase], _custom_config(allow_discounts=True))
        self.assertEqual(summary.category_gross("hair"), Decimal("9.00"))

    def test_multiple_configured_vat_rates(self):
        purchase = {
            "currency": "EUR",
            "products": [
                {"name": "Haarschnitt", "unitPrice": 11900, "quantity": "1",
                 "vatPercentage": 19},
                {"name": "Snack", "unitPrice": 10700, "quantity": "1",
                 "vatPercentage": 7},
            ],
            "payments": [{"amount": 22600, "type": "IZETTLE_CARD"}],
        }
        summary = aggregate([purchase], _custom_config())
        self.assertEqual(summary.total_gross, Decimal("226.00"))
        self.assertEqual(summary.total_vat, Decimal("26.00"))
        self.assertEqual(summary.category_gross("food"), Decimal("107.00"))

    def test_payment_product_mismatch_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1200)
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_owner_tip_uses_configured_revenue_and_vat(self):
        p = {
            "currency": "EUR",
            "amount": 1000,
            "userId": 101,
            "products": [{
                "name": "Haarschnitt", "unitPrice": 1000, "quantity": "1",
            }],
            "payments": [{
                "amount": 1200,
                "gratuityAmount": 200,
                "type": "IZETTLE_CARD",
            }],
        }
        summary = aggregate([p], _custom_config(tips=True))
        self.assertEqual(summary.category_gross("hair"), Decimal("10.00"))
        self.assertEqual(summary.category_gross("ownerTips"), Decimal("2.00"))
        self.assertEqual(summary.lines[("ownerTips", Decimal("19"))].card_gross,
                         Decimal("2.00"))

    def test_employee_tip_uses_zero_vat_category(self):
        p = {
            "currency": "EUR",
            "userId": "202",
            "products": [{
                "name": "Haarschnitt", "unitPrice": 1000, "quantity": "1",
            }],
            "payments": [{
                "amount": 1100,
                "gratuityAmount": 100,
                "type": "CASH",
            }],
        }
        summary = aggregate([p], _custom_config(tips=True))
        tip_line = summary.lines[("employeeTips", Decimal("0"))]
        self.assertEqual(tip_line.cash_gross, Decimal("1.00"))
        self.assertEqual(tip_line.cash_vat, Decimal("0.00"))

    def test_tips_from_multiple_cashiers_are_split_by_rule(self):
        owner = {
            "currency": "EUR", "userId": 101,
            "products": [{"name": "Haarschnitt", "unitPrice": 1000, "quantity": "1"}],
            "payments": [{"amount": 1100, "gratuityAmount": 100,
                          "type": "IZETTLE_CARD"}],
        }
        employee = {
            "currency": "EUR", "userId": 202,
            "products": [{"name": "Haarschnitt", "unitPrice": 1000, "quantity": "1"}],
            "payments": [{"amount": 1200, "gratuityAmount": 200,
                          "type": "IZETTLE_CARD"}],
        }
        summary = aggregate([owner, employee], _custom_config(tips=True))
        self.assertEqual(summary.category_gross("ownerTips"), Decimal("1.00"))
        self.assertEqual(summary.category_gross("employeeTips"), Decimal("2.00"))
        self.assertEqual(summary.total_gross, Decimal("23.00"))

    def test_tip_follows_its_payment_method(self):
        p = {
            "currency": "EUR", "userId": 202,
            "products": [{"name": "Haarschnitt", "unitPrice": 1000, "quantity": "1"}],
            "payments": [
                {"amount": 650, "gratuityAmount": 50, "type": "CASH"},
                {"amount": 450, "gratuityAmount": 50, "type": "IZETTLE_CARD"},
            ],
        }
        summary = aggregate([p], _custom_config(tips=True))
        tip_line = summary.lines[("employeeTips", Decimal("0"))]
        self.assertEqual(tip_line.cash_gross, Decimal("0.50"))
        self.assertEqual(tip_line.card_gross, Decimal("0.50"))
        self.assertEqual(summary.category_gross("hair"), Decimal("10.00"))

    def test_tip_is_rejected_without_configuration(self):
        p = _purchase(service_cents=1000, card_cents=1100)
        p["payments"][0]["gratuityAmount"] = 100
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_tip_for_unknown_cashier_is_rejected(self):
        p = {
            "currency": "EUR", "userId": 999,
            "products": [{"name": "Haarschnitt", "unitPrice": 1000, "quantity": "1"}],
            "payments": [{"amount": 1100, "gratuityAmount": 100,
                          "type": "IZETTLE_CARD"}],
        }
        with self.assertRaises(AggregationError):
            aggregate([p], _custom_config(tips=True))

    def test_tip_without_cashier_is_rejected(self):
        p = {
            "currency": "EUR",
            "products": [{"name": "Haarschnitt", "unitPrice": 1000, "quantity": "1"}],
            "payments": [{"amount": 1100, "gratuityAmount": 100,
                          "type": "IZETTLE_CARD"}],
        }
        with self.assertRaises(AggregationError):
            aggregate([p], _custom_config(tips=True))

    def test_tip_larger_than_payment_is_rejected(self):
        p = _purchase(service_cents=1000, card_cents=1000)
        p["payments"][0]["gratuityAmount"] = 1001
        with self.assertRaises(AggregationError):
            aggregate([p], _custom_config(tips=True))

    def test_products_without_payment_are_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": 1000, "quantity": "1"}],
             "payments": [], "currency": "EUR"}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_gift_card_sale_is_explicitly_blocked_even_if_name_is_mapped(self):
        p = {
            "currency": "EUR",
            "products": [{
                "name": "Haarschnitt",
                "type": "GIFTCARD",
                "unitPrice": 1000,
                "quantity": "1",
            }],
            "payments": [{"amount": 1000, "type": "CASH"}],
        }
        with self.assertRaisesRegex(AggregationError, "Gutscheinverkauf ist gesperrt"):
            aggregate([p], _custom_config())

    def test_gift_card_payment_is_explicitly_blocked(self):
        p = {
            "currency": "EUR",
            "products": [{
                "name": "Haarschnitt",
                "unitPrice": 1000,
                "quantity": "1",
            }],
            "payments": [{"amount": 1000, "type": "GIFTCARD"}],
        }
        policy = GiftCardPolicy(
            mode="reviewRequired",
            industry_profile="hairAndBeauty",
            redeemable_categories=frozenset({"hair"}),
            vat_rates=(Decimal("19"),),
        )
        with self.assertRaisesRegex(
            AggregationError, "Vorprüfung: Einzweckgutschein-Kandidat"
        ):
            aggregate([p], _custom_config(gift_cards=policy))

    def test_fractional_minor_payment_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1000)
        p["payments"][0]["amount"] = "1000.5"
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_non_finite_product_is_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": "NaN", "quantity": "1"}],
             "payments": [{"amount": 1000, "type": "CASH"}], "currency": "EUR"}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_refund_is_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": -1000, "quantity": "1"}],
             "payments": [{"amount": -1000, "type": "CASH"}], "currency": "EUR"}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_mixed_payment_cent_balancing(self):
        p = _purchase(service_cents=1, sales_cents=2, cash_cents=1, card_cents=2)
        s = aggregate([p])
        self.assertEqual(s.cash_gross, Decimal("0.01"))
        self.assertEqual(s.card_gross, Decimal("0.02"))
        self.assertEqual(s.service_gross, Decimal("0.01"))
        self.assertEqual(s.sales_gross, Decimal("0.02"))
        self.assertEqual(s.total_gross, Decimal("0.03"))

    def test_total_properties(self):
        s = aggregate([_purchase(service_cents=3800, sales_cents=3400, cash_cents=7200)])
        self.assertEqual(s.total_gross, s.cash_gross + s.card_gross)
        self.assertEqual(s.service_gross, s.cash_service_gross + s.card_service_gross)
        self.assertEqual(s.sales_gross,   s.cash_sales_gross   + s.card_sales_gross)

    def test_service_names_constant(self):
        self.assertIn("Dienstleistung", SERVICE_PRODUCT_NAMES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
