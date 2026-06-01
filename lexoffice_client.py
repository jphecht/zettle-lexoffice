import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from aggregator import DailySummary

logger = logging.getLogger(__name__)

LEXOFFICE_BASE_URL = "https://api.lexoffice.io/v1"
BERLIN_TZ = ZoneInfo("Europe/Berlin")
_REMARK_PREFIX = "zettle-import"


class LexofficeAPIError(Exception):
    pass


class LexofficeClient:
    def __init__(
        self,
        api_key: str,
        service_category_id: str,
        sales_category_id: str,
        contact_id: str,
    ) -> None:
        self._service_cat = service_category_id
        self._sales_cat   = sales_category_id
        self._contact_id  = contact_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ── Duplicate detection ───────────────────────────────────────────────────

    def vouchers_exist_for_date(self, business_date: date) -> dict[str, bool]:
        """Return which payment-type vouchers already exist for this date."""
        date_str  = business_date.isoformat()
        bar_mark  = f"{_REMARK_PREFIX}-{date_str}-bar"
        karte_mark = f"{_REMARK_PREFIX}-{date_str}-karte"
        result = {"bar": False, "karte": False}

        page = 0
        while True:
            data = self._get("/voucherlist", {
                "voucherType": "salesinvoice",
                "voucherStatus": "open,paid,voided,draft",
                "voucherDateFrom": date_str,
                "voucherDateTo": date_str,
                "page": page,
                "size": 25,
            })
            for item in data.get("content", []):
                full = self._get(f"/vouchers/{item['id']}", {})
                remark = full.get("remark", "")
                if bar_mark   in remark: result["bar"]   = True
                if karte_mark in remark: result["karte"] = True
                if result["bar"] and result["karte"]:
                    return result
                time.sleep(1)

            if data.get("last", True):
                break
            page += 1

        return result

    # ── Voucher creation ──────────────────────────────────────────────────────

    def create_daily_vouchers(
        self,
        business_date: date,
        summary: DailySummary,
        dry_run: bool = False,
    ) -> list[str]:
        """Create a Bar and/or Karte voucher for the day.

        Checks for existing vouchers first (skipped in dry-run).
        Returns the list of newly created voucher IDs.
        """
        existing = {"bar": False, "karte": False}
        if not dry_run:
            existing = self.vouchers_exist_for_date(business_date)

        created: list[str] = []

        if summary.has_cash and not existing["bar"]:
            vid = self._create_one(business_date, summary, "bar", dry_run)
            if vid:
                created.append(vid)

        if summary.has_card and not existing["karte"]:
            vid = self._create_one(business_date, summary, "karte", dry_run)
            if vid:
                created.append(vid)

        return created

    def _create_one(
        self,
        business_date: date,
        summary: DailySummary,
        suffix: str,
        dry_run: bool,
    ) -> Optional[str]:
        payload = self._build_payload(business_date, summary, suffix)
        if dry_run:
            label = "BAR" if suffix == "bar" else "KARTE"
            logger.info("[DRY-RUN] Would create %s voucher:\n%s",
                        label, json.dumps(payload, indent=2, ensure_ascii=False))
            return None
        data = self._post("/vouchers", payload)
        vid: str = data["id"]
        logger.info("Lexoffice %s voucher created for %s → id=%s",
                    suffix.upper(), business_date, vid)
        return vid

    def _build_payload(
        self, business_date: date, summary: DailySummary, suffix: str
    ) -> dict:
        voucher_dt = datetime(
            business_date.year, business_date.month, business_date.day,
            tzinfo=BERLIN_TZ,
        )
        date_str = business_date.isoformat()

        if suffix == "bar":
            items = _build_items(
                summary.cash_service_gross, summary.cash_service_vat,
                summary.cash_sales_gross,   summary.cash_sales_vat,
                self._service_cat, self._sales_cat,
            )
            label = "Bar"
        else:
            items = _build_items(
                summary.card_service_gross, summary.card_service_vat,
                summary.card_sales_gross,   summary.card_sales_vat,
                self._service_cat, self._sales_cat,
            )
            label = "Karte"

        total_gross = round(sum(i["amount"]    for i in items), 2)
        total_vat   = round(sum(i["taxAmount"] for i in items), 2)

        return {
            "type": "salesinvoice",
            "voucherDate": voucher_dt.isoformat(),
            "taxType": "gross",
            "totalGrossAmount": total_gross,
            "totalTaxAmount":   total_vat,
            "voucherNumber":    f"ZETTLE-{date_str}-{suffix.upper()}",
            "contactId":        self._contact_id,
            "introduction": (
                f"Tagessammelbeleg {label} {business_date.strftime('%d.%m.%Y')}"
            ),
            "remark": f"{_REMARK_PREFIX}-{date_str}-{suffix}",
            "voucherItems": items,
        }

    # ── Posting categories helper ─────────────────────────────────────────────

    def list_posting_categories(self) -> list[dict]:
        return self._get("/posting-categories", {})

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json_body=body)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        max_retries: int = 3,
    ) -> dict:
        url = LEXOFFICE_BASE_URL + path
        delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.request(
                    method, url, params=params, json=json_body, timeout=30
                )
                if resp.ok:
                    return resp.json() if resp.content else {}
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                raise LexofficeAPIError(
                    f"{method} {url} failed with HTTP {resp.status_code}: {resp.text}"
                )
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                if attempt == max_retries:
                    raise LexofficeAPIError(
                        f"{method} {url} failed after {max_retries} attempt(s): {exc}"
                    ) from exc
                logger.warning(
                    "Attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt, max_retries, exc, delay,
                )
                time.sleep(delay)
                delay *= 2

        raise LexofficeAPIError("Unexpected exit from retry loop")


# ── Payload helpers ───────────────────────────────────────────────────────────

def _build_items(
    service_gross: Decimal, service_vat: Decimal,
    sales_gross: Decimal,   sales_vat: Decimal,
    service_cat: str, sales_cat: str,
) -> list[dict]:
    items = []
    if service_gross:
        items.append(_item(service_gross, service_vat, service_cat))
    if sales_gross:
        items.append(_item(sales_gross, sales_vat, sales_cat))
    return items


def _item(gross: Decimal, _vat_ignored: Decimal, category_id: str) -> dict:
    # Recalculate VAT from the aggregated gross to avoid accumulated rounding
    # errors from per-product splits that Lexoffice rejects as invalid_taxamount.
    vat = (gross * Decimal("0.19") / Decimal("1.19")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "amount":         float(gross),
        "taxAmount":      float(vat),
        "taxRatePercent": 19.0,
        "categoryId":     category_id,
    }
