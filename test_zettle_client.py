"""Unit tests for ZettleClient — all HTTP calls are mocked."""
import json
import time
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from zettle_client import (
    CARD_PAYMENT_TYPES,
    ZettleAPIError,
    ZettleAuthError,
    ZettleClient,
    _fmt_dt,
)

CLIENT_ID  = "test-client-uuid"
FAKE_TOKEN = "fake-access-token-abc123"

# Minimal fake JWT: header.payload.signature (not verified in tests)
import base64 as _b64
_payload = _b64.urlsafe_b64encode(
    json.dumps({"client_id": CLIENT_ID}).encode()
).rstrip(b"=").decode()
API_KEY = f"eyJhbGciOiJSUzI1NiJ9.{_payload}.fakesig"


def _make_client() -> ZettleClient:
    return ZettleClient(API_KEY)


def _make_purchase(uuid: str, amount: int, payment_type: str) -> dict:
    return {
        "purchaseUUID": uuid,
        "timestamp": "2025-01-15T10:00:00.000+0000",
        "amount": amount,
        "currency": "EUR",
        "payments": [{"uuid": f"pay-{uuid}", "amount": amount, "type": payment_type}],
    }


def _token_post_mock() -> MagicMock:
    """Successful token exchange response."""
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    m.json.return_value = {"access_token": FAKE_TOKEN, "expires_in": 7200}
    return m


def _purchases_resp(purchases, last_hash="", has_more=False) -> MagicMock:
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    m.json.return_value = {
        "purchases": purchases,
        "lastPurchaseHash": last_hash,
        "linkUrls": ["next"] if has_more else [],
    }
    return m


def _error_resp(status_code: int) -> MagicMock:
    m = MagicMock()
    m.ok = False
    m.status_code = status_code
    m.text = f"Simulated {status_code} error"
    m.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return m


# ── client_id extraction & token exchange ─────────────────────────────────────

class TestClientIdExtraction(unittest.TestCase):
    def test_client_id_extracted_from_jwt(self):
        client = _make_client()
        self.assertEqual(client._client_id, CLIENT_ID)

    @patch("zettle_client.requests.post")
    def test_token_exchange_sends_correct_fields(self, mock_post):
        mock_post.return_value = _token_post_mock()
        client = _make_client()
        client._fetch_token()
        data = mock_post.call_args[1]["data"]
        self.assertEqual(data["grant_type"], "urn:ietf:params:oauth:grant-type:jwt-bearer")
        self.assertEqual(data["client_id"], CLIENT_ID)
        self.assertEqual(data["assertion"], API_KEY)

    @patch("zettle_client.requests.post")
    def test_fetch_token_stores_access_token(self, mock_post):
        mock_post.return_value = _token_post_mock()
        client = _make_client()
        client._fetch_token()
        self.assertEqual(client._access_token, FAKE_TOKEN)

    @patch("zettle_client.requests.post")
    def test_fetch_token_sets_expiry(self, mock_post):
        mock_post.return_value = _token_post_mock()
        client = _make_client()
        before = time.time()
        client._fetch_token()
        self.assertAlmostEqual(client._token_expires_at, before + 7140, delta=5)

    @patch("zettle_client.requests.post")
    def test_fetch_token_raises_on_error(self, mock_post):
        m = MagicMock()
        m.ok = False
        m.status_code = 401
        m.text = "Unauthorized"
        mock_post.return_value = m
        with self.assertRaises(ZettleAuthError):
            _make_client()._fetch_token()

    @patch("zettle_client.requests.post")
    def test_ensure_token_fetches_only_once_while_valid(self, mock_post):
        mock_post.return_value = _token_post_mock()
        client = _make_client()
        client._ensure_token()
        client._ensure_token()
        mock_post.assert_called_once()

    @patch("zettle_client.requests.post")
    def test_ensure_token_refreshes_when_expired(self, mock_post):
        mock_post.return_value = _token_post_mock()
        client = _make_client()
        client._ensure_token()
        client._token_expires_at = time.time() - 1
        client._ensure_token()
        self.assertEqual(mock_post.call_count, 2)


# ── Purchase retrieval ────────────────────────────────────────────────────────

class TestGetPurchasesForDay(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()
        self.test_date = date(2025, 1, 15)

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_single_page_returns_all_purchases(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _purchases_resp([
            _make_purchase("uuid-1", 2500, "IZETTLE_CARD"),
            _make_purchase("uuid-2", 800,  "CASH"),
        ])
        result = self.client.get_purchases_for_day(self.test_date)
        self.assertEqual(len(result), 2)

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_sends_correct_date_params(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _purchases_resp([])
        self.client.get_purchases_for_day(self.test_date)
        params = mock_get.call_args[1]["params"]
        self.assertEqual(params["startDate"], "2025-01-14T23:00:00.000+0000")
        self.assertEqual(params["stopDate"],  "2025-01-15T23:00:00.000+0000")

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_summer_date_uses_berlin_dst(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _purchases_resp([])
        self.client.get_purchases_for_day(date(2025, 7, 15))
        params = mock_get.call_args[1]["params"]
        self.assertEqual(params["startDate"], "2025-07-14T22:00:00.000+0000")
        self.assertEqual(params["stopDate"],  "2025-07-15T22:00:00.000+0000")

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_dst_start_day_has_23_hours(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _purchases_resp([])
        self.client.get_purchases_for_day(date(2025, 3, 30))
        params = mock_get.call_args[1]["params"]
        self.assertEqual(params["startDate"], "2025-03-29T23:00:00.000+0000")
        self.assertEqual(params["stopDate"],  "2025-03-30T22:00:00.000+0000")

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_empty_day_returns_empty_list(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _purchases_resp([])
        self.assertEqual(self.client.get_purchases_for_day(self.test_date), [])

    @patch("zettle_client.time.sleep")
    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_pagination_fetches_all_pages(self, mock_get, mock_post, _sleep):
        mock_post.return_value = _token_post_mock()
        page1 = [_make_purchase(f"p1-{i}", 1000, "IZETTLE_CARD") for i in range(1000)]
        page2 = [_make_purchase("p2-1", 500, "CASH")]
        mock_get.side_effect = [
            _purchases_resp(page1, last_hash="hash-abc", has_more=True),
            _purchases_resp(page2),
        ]
        result = self.client.get_purchases_for_day(self.test_date)
        self.assertEqual(len(result), 1001)
        self.assertEqual(mock_get.call_args_list[1][1]["params"]["lastPurchaseHash"], "hash-abc")

    @patch("zettle_client.time.sleep")
    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_pagination_without_hash_is_rejected(self, mock_get, mock_post, _sleep):
        mock_post.return_value = _token_post_mock()
        page = [_make_purchase(f"p-{i}", 1000, "CASH") for i in range(1000)]
        mock_get.return_value = _purchases_resp(page, has_more=True)
        with self.assertRaises(ZettleAPIError):
            self.client.get_purchases_for_day(self.test_date)

    @patch("zettle_client.time.sleep")
    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_repeated_pagination_hash_is_rejected(self, mock_get, mock_post, _sleep):
        mock_post.return_value = _token_post_mock()
        page = [_make_purchase(f"p-{i}", 1000, "CASH") for i in range(1000)]
        mock_get.side_effect = [
            _purchases_resp(page, last_hash="same", has_more=True),
            _purchases_resp(page, last_hash="same", has_more=True),
        ]
        with self.assertRaises(ZettleAPIError):
            self.client.get_purchases_for_day(self.test_date)

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_missing_timestamp_is_rejected(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        purchase = _make_purchase("no-time", 1000, "CASH")
        del purchase["timestamp"]
        mock_get.return_value = _purchases_resp([purchase])
        with self.assertRaises(ZettleAPIError):
            self.client.get_purchases_for_day(self.test_date)

    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_timestamp_without_timezone_is_rejected(self, mock_get, mock_post):
        mock_post.return_value = _token_post_mock()
        purchase = _make_purchase("naive-time", 1000, "CASH")
        purchase["timestamp"] = "2025-01-15T10:00:00"
        mock_get.return_value = _purchases_resp([purchase])
        with self.assertRaises(ZettleAPIError):
            self.client.get_purchases_for_day(self.test_date)

    @patch("zettle_client.time.sleep")
    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_retry_on_500_raises_after_max_attempts(self, mock_get, mock_post, _sleep):
        mock_post.return_value = _token_post_mock()
        mock_get.return_value = _error_resp(500)
        with self.assertRaises(ZettleAPIError):
            self.client.get_purchases_for_day(self.test_date)
        self.assertEqual(mock_get.call_count, 3)

    @patch("zettle_client.time.sleep")
    @patch("zettle_client.requests.post")
    @patch("zettle_client.requests.get")
    def test_retry_succeeds_on_second_attempt(self, mock_get, mock_post, _sleep):
        mock_post.return_value = _token_post_mock()
        good = _purchases_resp([_make_purchase("ok", 1000, "IZETTLE_CARD")])
        mock_get.side_effect = [_error_resp(503), good]
        self.assertEqual(len(self.client.get_purchases_for_day(self.test_date)), 1)


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestHelpers(unittest.TestCase):
    def test_fmt_dt_format(self):
        from datetime import datetime, timezone
        dt = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(_fmt_dt(dt), "2025-01-15T00:00:00.000+0000")

    def test_card_payment_types(self):
        self.assertIn("IZETTLE_CARD", CARD_PAYMENT_TYPES)
        self.assertIn("PAYPAL", CARD_PAYMENT_TYPES)
        self.assertNotIn("CASH", CARD_PAYMENT_TYPES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
