"""Unit tests for LexofficeClient — all HTTP calls are mocked."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests

from aggregator import DailySummary
from lexoffice_client import (
    LEXOFFICE_BASE_URL,
    LexofficeAPIError,
    LexofficeAmbiguousWriteError,
    LexofficeClient,
    _build_items,
)

API_KEY     = "test-api-key"
SERVICE_CAT = "service-cat-uuid"
SALES_CAT   = "sales-cat-uuid"
CONTACT_ID  = "contact-uuid"

TEST_DATE     = date(2025, 1, 15)
TEST_DATE_STR = "2025-01-15"
BAR_MARKER   = f"zettle-import-{TEST_DATE_STR}-bar"
KARTE_MARKER = f"zettle-import-{TEST_DATE_STR}-karte"
BAR_REFUND_MARKER = f"zettle-import-{TEST_DATE_STR}-bar-refund"
KARTE_REFUND_MARKER = f"zettle-import-{TEST_DATE_STR}-karte-refund"


def _make_client() -> LexofficeClient:
    return LexofficeClient(
        API_KEY, {"service": SERVICE_CAT, "sales": SALES_CAT}, CONTACT_ID
    )


def _summary(
    cash_service=0, cash_sales=0,
    card_service=0, card_sales=0,
    refund_cash_service=0, refund_card_service=0,
) -> DailySummary:
    summary = DailySummary()
    summary.add("cash", "service", Decimal("19"), Decimal(str(cash_service)))
    summary.add("cash", "sales", Decimal("19"), Decimal(str(cash_sales)))
    summary.add("card", "service", Decimal("19"), Decimal(str(card_service)))
    summary.add("card", "sales", Decimal("19"), Decimal(str(card_sales)))
    summary.add_refund(
        "cash", "service", Decimal("19"), Decimal(str(refund_cash_service))
    )
    summary.add_refund(
        "card", "service", Decimal("19"), Decimal(str(refund_card_service))
    )
    return summary


def _ok(body: dict) -> MagicMock:
    m = MagicMock(); m.ok = True; m.status_code = 200
    m.content = True; m.json.return_value = body; return m


def _err(status: int) -> MagicMock:
    m = MagicMock(); m.ok = False; m.status_code = status
    m.content = b"err"; m.raise_for_status.side_effect = Exception(f"HTTP {status}"); return m


# ── Payload building ──────────────────────────────────────────────────────────

class TestBuildPayload(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()
        self.s = _summary(cash_service=128, cash_sales=72)

    def _bar(self): return self.client._build_payload(TEST_DATE, self.s, "bar")
    def _karte(self): return self.client._build_payload(TEST_DATE, _summary(card_service=50), "karte")

    def test_type_salesinvoice(self):
        self.assertEqual(self._bar()["type"], "salesinvoice")

    def test_tax_type_gross(self):
        self.assertEqual(self._bar()["taxType"], "gross")

    def test_required_fields(self):
        p = self._bar()
        for f in ["totalGrossAmount", "totalTaxAmount", "voucherNumber", "contactId"]:
            self.assertIn(f, p)

    def test_bar_voucher_number(self):
        self.assertIn("BAR", self._bar()["voucherNumber"])
        self.assertIn(TEST_DATE_STR, self._bar()["voucherNumber"])

    def test_karte_voucher_number(self):
        self.assertIn("KARTE", self._karte()["voucherNumber"])

    def test_bar_remark_marker(self):
        self.assertEqual(self._bar()["remark"], BAR_MARKER)

    def test_karte_remark_marker(self):
        self.assertEqual(self._karte()["remark"], KARTE_MARKER)

    def test_contact_id(self):
        self.assertEqual(self._bar()["contactId"], CONTACT_ID)

    def test_bar_introduction_contains_bar(self):
        self.assertIn("Bar", self._bar()["introduction"])

    def test_karte_introduction_contains_karte(self):
        self.assertIn("Karte", self._karte()["introduction"])

    def test_total_gross_matches_items(self):
        p = self._bar()
        self.assertAlmostEqual(p["totalGrossAmount"], 200.0, places=2)

    def test_total_vat_matches_items(self):
        p = self._bar()
        expected_vat = round(sum(i["taxAmount"] for i in p["voucherItems"]), 2)
        self.assertAlmostEqual(p["totalTaxAmount"], expected_vat, places=2)

    def test_bar_uses_cash_amounts(self):
        p = self._bar()
        amounts = [i["amount"] for i in p["voucherItems"]]
        self.assertAlmostEqual(sum(amounts), 200.0, places=2)  # cash total

    def test_voucher_date_uses_documented_date_format(self):
        self.assertEqual(self._bar()["voucherDate"], TEST_DATE_STR)

    def test_refund_payload_is_separate_sales_credit_note(self):
        summary = _summary(refund_card_service=59.50)
        payload = self.client._build_payload(
            TEST_DATE, summary, "karte", refund=True
        )
        self.assertEqual(payload["type"], "salescreditnote")
        self.assertEqual(payload["totalGrossAmount"], 59.50)
        self.assertEqual(payload["remark"], KARTE_REFUND_MARKER)
        self.assertIn("REFUND", payload["voucherNumber"])
        self.assertIn("Tagesgutschrift", payload["introduction"])


class TestBuildItems(unittest.TestCase):
    def test_empty_when_all_zero(self):
        self.assertEqual(_build_items(DailySummary(), "cash", {
            "service": SERVICE_CAT, "sales": SALES_CAT
        }), [])

    def test_service_category(self):
        items = _build_items(_summary(cash_service=119), "cash", {
            "service": SERVICE_CAT, "sales": SALES_CAT
        })
        self.assertEqual(items[0]["categoryId"], SERVICE_CAT)

    def test_sales_category(self):
        items = _build_items(_summary(cash_sales=59), "cash", {
            "service": SERVICE_CAT, "sales": SALES_CAT
        })
        self.assertEqual(items[0]["categoryId"], SALES_CAT)

    def test_two_items_when_both_nonzero(self):
        items = _build_items(_summary(cash_service=100, cash_sales=50), "cash", {
            "service": SERVICE_CAT, "sales": SALES_CAT
        })
        self.assertEqual(len(items), 2)

    def test_configured_vat_rate_is_used(self):
        summary = DailySummary()
        summary.add("cash", "food", Decimal("7"), Decimal("107"))
        item = _build_items(summary, "cash", {"food": "food-cat"})[0]
        self.assertEqual(item["taxRatePercent"], 7.0)
        self.assertEqual(item["taxAmount"], 7.0)

    def test_missing_category_id_is_rejected(self):
        summary = DailySummary()
        summary.add("cash", "unknown", Decimal("19"), Decimal("119"))
        with self.assertRaises(LexofficeAPIError):
            _build_items(summary, "cash", {})

    def test_refund_items_use_positive_credit_amounts(self):
        summary = _summary(refund_cash_service=119)
        items = _build_items(
            summary,
            "cash",
            {"service": SERVICE_CAT},
            refund=True,
        )
        self.assertEqual(items[0]["amount"], 119.0)
        self.assertEqual(items[0]["taxAmount"], 19.0)


# ── create_daily_vouchers ─────────────────────────────────────────────────────

class TestCreateDailyVouchers(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()

    def _patch(self):
        return patch.object(self.client._session, "request")

    def test_duplicate_search_includes_every_status(self):
        with self._patch() as mock_req:
            mock_req.return_value = _ok({"content": [], "last": True})
            self.client.vouchers_exist_for_date(TEST_DATE)
        self.assertEqual(mock_req.call_args.kwargs["params"]["voucherStatus"], "any")

    def test_duplicate_search_includes_sales_and_credit_notes(self):
        with self._patch() as mock_req:
            mock_req.return_value = _ok({"content": [], "last": True})
            self.client.vouchers_exist_for_date(TEST_DATE)
        self.assertEqual(
            mock_req.call_args.kwargs["params"]["voucherType"],
            "salesinvoice,salescreditnote",
        )

    def test_cash_only_creates_bar_voucher(self):
        s = _summary(cash_service=100)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),   # vouchers_exist list
                _ok({"id": "bar-id"}),                # create bar
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(ids, ["bar-id"])

    def test_card_only_creates_karte_voucher(self):
        s = _summary(card_service=100)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _ok({"id": "karte-id"}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(ids, ["karte-id"])

    def test_both_creates_two_vouchers(self):
        s = _summary(cash_service=100, card_service=50)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _ok({"id": "bar-id"}),
                _ok({"id": "karte-id"}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(ids, ["bar-id", "karte-id"])

    def test_refund_only_creates_sales_credit_note(self):
        summary = _summary(refund_card_service=50)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _ok({"id": "refund-id"}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, summary)
        self.assertEqual(ids, ["refund-id"])
        payload = mock_req.call_args.kwargs["json"]
        self.assertEqual(payload["type"], "salescreditnote")

    def test_sale_and_refund_create_independent_vouchers(self):
        summary = _summary(card_service=100, refund_card_service=25)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _ok({"id": "sale-id"}),
                _ok({"id": "refund-id"}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, summary)
        self.assertEqual(ids, ["sale-id", "refund-id"])
        posted_types = [
            call.kwargs["json"]["type"]
            for call in mock_req.call_args_list
            if call.args[0] == "POST"
        ]
        self.assertEqual(posted_types, ["salesinvoice", "salescreditnote"])

    @patch("lexoffice_client.time.sleep")
    def test_existing_refund_does_not_hide_sale_or_duplicate_credit(self, _sleep):
        summary = _summary(card_service=100, refund_card_service=25)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [{"id": "existing-refund"}], "last": True}),
                _ok({"id": "existing-refund", "remark": KARTE_REFUND_MARKER}),
                _ok({"id": "sale-id"}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, summary)
        self.assertEqual(ids, ["sale-id"])
        posted = [
            call.kwargs["json"]
            for call in mock_req.call_args_list
            if call.args[0] == "POST"
        ]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["type"], "salesinvoice")

    def test_dry_run_makes_no_http_call(self):
        s = _summary(cash_service=100, card_service=50)
        with self._patch() as mock_req:
            ids = self.client.create_daily_vouchers(TEST_DATE, s, dry_run=True)
        mock_req.assert_not_called()
        self.assertEqual(ids, [])

    @patch("lexoffice_client.time.sleep")
    def test_skips_existing_bar(self, _sleep):
        s = _summary(cash_service=100, card_service=50)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [{"id": "v1"}], "last": True}),
                _ok({"id": "v1", "remark": BAR_MARKER}),  # bar exists
                _ok({"id": "karte-id"}),                   # create karte
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(ids, ["karte-id"])

    @patch("lexoffice_client.time.sleep")
    def test_returns_empty_when_all_exist(self, _sleep):
        s = _summary(cash_service=100, card_service=50)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [{"id": "v1"}, {"id": "v2"}], "last": True}),
                _ok({"id": "v1", "remark": BAR_MARKER}),
                _ok({"id": "v2", "remark": KARTE_MARKER}),
            ]
            ids = self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(ids, [])

    def test_post_503_is_not_retried(self):
        s = _summary(cash_service=100)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _err(503),
            ]
            with self.assertRaises(LexofficeAmbiguousWriteError):
                self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(mock_req.call_count, 2)

    def test_post_timeout_is_not_retried(self):
        s = _summary(cash_service=100)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                requests.Timeout("simulated"),
            ]
            with self.assertRaises(LexofficeAmbiguousWriteError):
                self.client.create_daily_vouchers(TEST_DATE, s)
        self.assertEqual(mock_req.call_count, 2)

    def test_post_response_without_id_is_rejected(self):
        s = _summary(cash_service=100)
        with self._patch() as mock_req:
            mock_req.side_effect = [
                _ok({"content": [], "last": True}),
                _ok({}),
            ]
            with self.assertRaises(LexofficeAPIError):
                self.client.create_daily_vouchers(TEST_DATE, s)

    def test_current_lexware_api_domain(self):
        self.assertEqual(LEXOFFICE_BASE_URL, "https://api.lexware.io/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
