import base64
import json
import time
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ZETTLE_TOKEN_URL    = "https://oauth.zettle.com/token"
ZETTLE_PURCHASE_API = "https://purchase.izettle.com/purchases/v2"
ZETTLE_PRODUCT_API  = "https://products.izettle.com/organizations/self/products/v2"

_TOKEN_REFRESH_MARGIN = 60  # seconds before expiry to refresh proactively

CARD_PAYMENT_TYPES = {
    "IZETTLE_CARD",
    "IZETTLE_CARD_ONLINE",
    "PAYPAL",
    "MOBILE_PAY",
    "VIPPS",
    "SWISH",
}
CASH_PAYMENT_TYPES = {"CASH", "IZETTLE_CASH"}
BERLIN_TZ = ZoneInfo("Europe/Berlin")


class ZettleAPIError(Exception):
    pass


class ZettleAuthError(ZettleAPIError):
    pass


class ZettleClient:
    def __init__(self, api_key: str) -> None:
        self._api_key  = api_key
        self._client_id = _extract_client_id(api_key)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ── Token exchange ────────────────────────────────────────────────────────

    def _fetch_token(self) -> None:
        """Exchange the long-lived API key for a short-lived access token."""
        logger.debug("Exchanging Zettle API key for access token …")
        try:
            resp = requests.post(
                ZETTLE_TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "client_id":  self._client_id,
                    "assertion":  self._api_key,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ZettleAuthError(f"Token exchange network error: {exc}") from exc

        if not resp.ok:
            raise ZettleAuthError(
                f"Token exchange failed (HTTP {resp.status_code})"
            )

        body = resp.json()
        self._access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 7200))
        self._token_expires_at = time.time() + expires_in - _TOKEN_REFRESH_MARGIN
        logger.debug("Zettle access token acquired, valid for %ds", expires_in)

    def _ensure_token(self) -> str:
        if not self._access_token or time.time() >= self._token_expires_at:
            self._fetch_token()
        return self._access_token  # type: ignore[return-value]

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_token()}"}

    # ── HTTP GET with retry / exponential backoff ─────────────────────────────

    def _get(self, url: str, params: dict, max_retries: int = 3) -> dict:
        delay = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(
                    url, headers=self._auth_headers(), params=params, timeout=30
                )
                if resp.status_code == 401 and attempt == 1:
                    logger.warning("HTTP 401 — forcing token refresh and retrying")
                    self._access_token = None
                    resp = requests.get(
                        url, headers=self._auth_headers(), params=params, timeout=30
                    )
                if resp.ok:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                if attempt == max_retries:
                    raise ZettleAPIError(
                        f"GET {url} failed after {max_retries} attempt(s): {exc}"
                    ) from exc
                logger.warning(
                    "Attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt, max_retries, exc, delay,
                )
                time.sleep(delay)
                delay *= 2

        raise ZettleAPIError("Unexpected exit from retry loop")

    # ── Purchase retrieval ────────────────────────────────────────────────────

    def get_purchases_for_day(self, business_date: date) -> list[dict]:
        """Return all purchases for a Europe/Berlin business day.

        The Zettle API ignores stopDate and returns all purchases from startDate
        onwards, so we filter the results by timestamp in Python.
        """
        local_start = datetime(
            business_date.year, business_date.month, business_date.day,
            0, 0, 0, tzinfo=BERLIN_TZ,
        )
        local_stop = local_start + timedelta(days=1)
        start_dt = local_start.astimezone(timezone.utc)
        stop_dt = local_stop.astimezone(timezone.utc)

        purchases: list[dict] = []
        last_hash: Optional[str] = None
        seen_hashes: set[str] = set()

        while True:
            params: dict = {
                "startDate": _fmt_dt(start_dt),
                "stopDate":  _fmt_dt(stop_dt),
                "limit": 1000,
            }
            if last_hash:
                params["lastPurchaseHash"] = last_hash

            data = self._get(ZETTLE_PURCHASE_API, params)
            page = data.get("purchases", [])
            if not isinstance(page, list):
                raise ZettleAPIError("Zettle-Antwort enthält keine gültige Kaufliste")

            in_range = [p for p in page if _purchase_in_range(p, start_dt, stop_dt)]
            purchases.extend(in_range)

            # API returns ascending timestamps; if any purchase is past our window
            # all subsequent pages will be too — stop early.
            if _any_past_range(page, stop_dt) or len(page) < 1000 or not data.get("linkUrls"):
                break

            next_hash = data.get("lastPurchaseHash")
            if not isinstance(next_hash, str) or not next_hash:
                raise ZettleAPIError(
                    "Zettle meldet weitere Seiten ohne lastPurchaseHash"
                )
            if next_hash == last_hash or next_hash in seen_hashes:
                raise ZettleAPIError(
                    "Zettle-Paginierung wiederholt lastPurchaseHash"
                )
            seen_hashes.add(next_hash)
            last_hash = next_hash
            logger.debug("Paginating Zettle results, lastPurchaseHash=%s", last_hash)
            time.sleep(1)

        logger.info("Zettle: fetched %d purchase(s) for %s", len(purchases), business_date)
        return purchases

    # ── Product library (read-only setup review) ─────────────────────────────

    def get_products(self) -> list[dict]:
        """Return the merchant's product library using READ:PRODUCT only."""
        data = self._get(ZETTLE_PRODUCT_API, {})
        if isinstance(data, list):
            products = data
        elif isinstance(data, dict):
            products = data.get("products")
        else:
            products = None
        if not isinstance(products, list) or not all(
            isinstance(product, dict) for product in products
        ):
            raise ZettleAPIError(
                "Zettle-Antwort enthält keine gültige Produktliste"
            )
        logger.info("Zettle: fetched %d catalog product(s)", len(products))
        return products


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_client_id(api_key: str) -> str:
    """Extract client_id from the JWT payload of the Zettle API key."""
    try:
        payload_part = api_key.split(".")[1]
        padding = "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part + padding))
        return payload["client_id"]
    except Exception as exc:
        raise ZettleAuthError(f"Could not extract client_id from API key: {exc}") from exc


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _parse_purchase_ts(purchase: dict) -> datetime:
    if not isinstance(purchase, dict):
        raise ZettleAPIError("Ungültiger Zettle-Kauf in API-Antwort")
    ts_str = purchase.get("timestamp") or purchase.get("created")
    if not ts_str:
        raise ZettleAPIError("Zettle-Kauf ohne Zeitstempel")
    try:
        dt = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone missing")
        return dt
    except (ValueError, TypeError) as exc:
        raise ZettleAPIError("Zettle-Kauf mit ungültigem Zeitstempel") from exc


def _purchase_in_range(purchase: dict, start: datetime, stop: datetime) -> bool:
    ts = _parse_purchase_ts(purchase)
    return start <= ts < stop


def _any_past_range(page: list[dict], stop: datetime) -> bool:
    return any(_parse_purchase_ts(p) >= stop for p in page)
