"""Unit tests for the aggregator module."""
import unittest
from decimal import Decimal

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
    return {"products": products, "payments": payments}


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

    def test_real_day_26_may(self):
        # 4 Käufe: total 200€, alles bar
        purchases = [
            _purchase(service_cents=3800, sales_cents=3400, cash_cents=7200),
            _purchase(service_cents=3500, cash_cents=3500),
            _purchase(sales_cents=3800, cash_cents=3800),
            _purchase(service_cents=5500, cash_cents=5500),
        ]
        s = aggregate(purchases)
        self.assertEqual(s.service_gross, Decimal("128.00"))
        self.assertEqual(s.sales_gross,   Decimal("72.00"))
        self.assertEqual(s.total_gross,   Decimal("200.00"))
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
             "payments": [{"amount": 5000, "type": "CASH"}]}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_non_dict_product_is_rejected(self):
        p = {"products": [True, None, {"name": "Dienstleistung", "unitPrice": 1000, "quantity": "1"}],
             "payments": [{"amount": 1000, "type": "CASH"}]}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_unknown_payment_type_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1000)
        p["payments"][0]["type"] = "MYSTERY"
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_payment_product_mismatch_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1200)
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_products_without_payment_are_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": 1000, "quantity": "1"}],
             "payments": []}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_fractional_minor_payment_is_rejected(self):
        p = _purchase(service_cents=1000, cash_cents=1000)
        p["payments"][0]["amount"] = "1000.5"
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_non_finite_product_is_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": "NaN", "quantity": "1"}],
             "payments": [{"amount": 1000, "type": "CASH"}]}
        with self.assertRaises(AggregationError):
            aggregate([p])

    def test_refund_is_rejected(self):
        p = {"products": [{"name": "Verkauf", "unitPrice": -1000, "quantity": "1"}],
             "payments": [{"amount": -1000, "type": "CASH"}]}
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
