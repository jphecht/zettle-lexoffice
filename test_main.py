"""Integration-style tests for main.py."""
import argparse
import io
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import main as main_module
from accounting_config import AccountingConfig, DEFAULT_CONFIG, GiftCardPolicy
from main import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    _build_date_range,
    _gift_card_review_detail,
    _process_day,
)

TODAY = date(2025, 6, 1)


def _purchase(service_cents=0, sales_cents=0, cash_cents=None, card_cents=0) -> dict:
    products = []
    if service_cents:
        products.append({"name": "Dienstleistung", "unitPrice": service_cents, "quantity": "1"})
    if sales_cents:
        products.append({"name": "Verkauf", "unitPrice": sales_cents, "quantity": "1"})
    if cash_cents is None:
        cash_cents = service_cents + sales_cents
    payments = []
    if cash_cents:
        payments.append({"amount": cash_cents, "type": "CASH"})
    if card_cents:
        payments.append({"amount": card_cents, "type": "IZETTLE_CARD"})
    return {"currency": "EUR", "products": products, "payments": payments}


def _mock_zettle(purchases):
    m = MagicMock(); m.get_purchases_for_day.return_value = purchases; return m


def _mock_lexoffice(created=None):
    m = MagicMock()
    m.create_daily_vouchers.return_value = created if created is not None else ["new-id"]
    return m


def _refund_purchase(amount_cents=1000) -> dict:
    return {
        "currency": "EUR",
        "amount": -amount_cents,
        "refund": True,
        "refundsPurchaseUUID1": "original-purchase",
        "products": [{
            "name": "Dienstleistung",
            "unitPrice": amount_cents,
            "quantity": "-1",
            "grossValue": -amount_cents,
        }],
        "payments": [{
            "amount": -amount_cents,
            "type": "IZETTLE_CARD",
            "references": {"refundsPayment": "original-payment"},
        }],
    }


# ── _build_date_range ─────────────────────────────────────────────────────────

class TestBuildDateRange(unittest.TestCase):
    def _args(self, **kw):
        import argparse
        defaults = {"date": None, "from_date": None, "to_date": None, "dry_run": False}
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_single_date(self):
        self.assertEqual(_build_date_range(self._args(date="2025-01-15")), [date(2025, 1, 15)])

    def test_range_inclusive(self):
        days = _build_date_range(self._args(from_date="2025-01-01", to_date="2025-01-03"))
        self.assertEqual(days, [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)])

    def test_range_five_months(self):
        days = _build_date_range(self._args(from_date="2025-01-01", to_date="2025-05-31"))
        self.assertEqual(len(days), 151)

    @patch("main.date")
    def test_default_is_yesterday(self, mock_date):
        mock_date.today.return_value = TODAY
        mock_date.fromisoformat = date.fromisoformat
        import argparse
        args = argparse.Namespace(date=None, from_date=None, to_date=None, dry_run=False)
        self.assertEqual(_build_date_range(args), [TODAY - timedelta(days=1)])


class TestGiftCardReview(unittest.TestCase):
    def test_rejected_gift_cards_are_reported(self):
        self.assertIn("gesperrt", _gift_card_review_detail(DEFAULT_CONFIG))

    def test_review_reports_industry_rates_and_candidate(self):
        policy = GiftCardPolicy(
            mode="reviewRequired",
            industry_profile="gastronomy",
            redeemable_categories=frozenset({"food", "drinks"}),
            vat_rates=(Decimal("7"), Decimal("19")),
        )
        config = AccountingConfig(
            currency=DEFAULT_CONFIG.currency,
            product_rules=DEFAULT_CONFIG.product_rules,
            category_environment=DEFAULT_CONFIG.category_environment,
            gift_cards=policy,
        )
        detail = _gift_card_review_detail(config)
        self.assertIn("gastronomy", detail)
        self.assertIn("7%", detail)
        self.assertIn("19%", detail)
        self.assertIn("Mehrzweckgutschein-Kandidat", detail)
        self.assertIn("Keine Buchungsfreigabe", detail)


class TestCatalogReviewCommand(unittest.TestCase):
    @patch("main.load_dotenv")
    @patch("main._setup_logging")
    @patch("main.ZettleClient")
    @patch("main._parse_args")
    def test_catalog_review_is_read_only_and_skips_lexware(
        self, mock_args, mock_zettle_class, mock_setup_logging, _mock_load_dotenv
    ):
        mock_args.return_value = argparse.Namespace(
            date=None,
            from_date=None,
            to_date=None,
            dry_run=False,
            review_gift_cards=False,
            review_catalog=True,
            propose_config=False,
        )
        mock_zettle_class.return_value.get_products.return_value = [
            {"name": "Dienstleistung", "variants": [{"vatPercentage": 19}]},
            {"name": "Beispiel Zusatzleistung", "variants": [{"vatPercentage": 19}]},
            {"name": "Verkauf", "variants": [{"vatPercentage": 19}]},
        ]
        with patch.dict("main.os.environ", {"ZETTLE_API_KEY": "test"}, clear=True):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                main_module.main()

        self.assertIn("Katalogprüfung ohne Blocker", stdout.getvalue())
        mock_zettle_class.return_value.get_products.assert_called_once_with()
        mock_setup_logging.assert_not_called()

    @patch("main.load_dotenv")
    @patch("main._setup_logging")
    @patch("main.ZettleClient")
    @patch("main._parse_args")
    def test_config_proposal_is_json_and_skips_lexware(
        self, mock_args, mock_zettle_class, mock_setup_logging, _mock_load_dotenv
    ):
        mock_args.return_value = argparse.Namespace(
            date=None,
            from_date=None,
            to_date=None,
            dry_run=False,
            review_gift_cards=False,
            review_catalog=False,
            propose_config=True,
        )
        mock_zettle_class.return_value.get_products.return_value = [
            {"name": "Dienstleistung", "variants": [{"vatPercentage": 19}]},
        ]
        with patch.dict("main.os.environ", {"ZETTLE_API_KEY": "test"}, clear=True):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                main_module.main()

        proposal = __import__("json").loads(stdout.getvalue())
        self.assertEqual(proposal["proposalStatus"], "reviewRequired")
        mock_setup_logging.assert_not_called()


# ── _process_day ──────────────────────────────────────────────────────────────

class TestProcessDay(unittest.TestCase):
    def test_ok_cash_only(self):
        zettle    = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice(["bar-id"])
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_OK)
        self.assertIn("119.00", detail)
        lexoffice.create_daily_vouchers.assert_called_once()

    def test_ok_card_only(self):
        zettle    = _mock_zettle([_purchase(service_cents=5950, cash_cents=0, card_cents=5950)])
        lexoffice = _mock_lexoffice(["karte-id"])
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_OK)
        self.assertIn("59.50", detail)

    def test_ok_mixed_payments(self):
        zettle = _mock_zettle([
            _purchase(service_cents=11900),
            _purchase(service_cents=5950, cash_cents=0, card_cents=5950),
        ])
        lexoffice = _mock_lexoffice(["bar-id", "karte-id"])
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_OK)
        self.assertIn("178.50", detail)

    def test_skipped_no_purchases(self):
        zettle    = _mock_zettle([])
        lexoffice = _mock_lexoffice()
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_SKIPPED)
        self.assertIn("kein Umsatz", detail)
        lexoffice.create_daily_vouchers.assert_not_called()

    def test_skipped_all_exist(self):
        zettle    = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice([])   # empty → all already existed
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_SKIPPED)
        self.assertIn("bereits vorhanden", detail)

    def test_dry_run_returns_ok(self):
        zettle    = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice([])
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=True)
        self.assertEqual(status, STATUS_OK)
        self.assertIn("DRY-RUN", detail)

    def test_dry_run_passes_flag(self):
        zettle    = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice([])
        _process_day(TODAY, zettle, lexoffice, dry_run=True)
        _, kwargs = lexoffice.create_daily_vouchers.call_args
        self.assertTrue(kwargs.get("dry_run"))

    def test_failed_on_zettle_error(self):
        from zettle_client import ZettleAPIError
        zettle = MagicMock()
        zettle.get_purchases_for_day.side_effect = ZettleAPIError("timeout")
        status, detail = _process_day(TODAY, zettle, _mock_lexoffice(), dry_run=False)
        self.assertEqual(status, STATUS_FAILED)
        self.assertIn("Zettle-Fehler", detail)

    def test_failed_on_lexoffice_error(self):
        from lexoffice_client import LexofficeAPIError
        zettle    = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice()
        lexoffice.create_daily_vouchers.side_effect = LexofficeAPIError("bad request")
        status, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertEqual(status, STATUS_FAILED)
        self.assertIn("Lexoffice-Fehler", detail)

    def test_failed_on_accounting_validation(self):
        purchase = _purchase(service_cents=1000)
        purchase["payments"][0]["amount"] = 1200
        status, detail = _process_day(
            TODAY, _mock_zettle([purchase]), _mock_lexoffice(), dry_run=False
        )
        self.assertEqual(status, STATUS_FAILED)
        self.assertIn("Buchungsprüfung", detail)

    def test_detail_shows_bar_karte_split(self):
        zettle = _mock_zettle([_purchase(service_cents=11900)])
        lexoffice = _mock_lexoffice(["bar-id"])
        _, detail = _process_day(TODAY, zettle, lexoffice, dry_run=False)
        self.assertIn("Bar", detail)
        self.assertIn("Karte", detail)

    def test_refund_detail_shows_separate_amount_and_net(self):
        config = replace(DEFAULT_CONFIG, refund_mode="separateCreditNote")
        lexoffice = _mock_lexoffice(["credit-id"])
        status, detail = _process_day(
            TODAY,
            _mock_zettle([_refund_purchase()]),
            lexoffice,
            dry_run=False,
            config=config,
        )
        self.assertEqual(status, STATUS_OK)
        self.assertIn("Erstattungen 10.00", detail)
        self.assertIn("Netto -10.00", detail)
        summary = lexoffice.create_daily_vouchers.call_args.args[1]
        self.assertEqual(summary.total_gross, Decimal("0"))
        self.assertEqual(summary.refund_total_gross, Decimal("10.00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
