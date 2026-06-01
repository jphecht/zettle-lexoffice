"""Aggregates Zettle purchases into a daily summary split by payment method × product type.

Each purchase amount is split into:
  - cash × service   (Barzahlung × Dienstleistung)
  - cash × sales     (Barzahlung × Warenverkauf)
  - card × service   (Kartenzahlung × Dienstleistung)
  - card × sales     (Kartenzahlung × Warenverkauf)

For purchases with mixed payment (partially cash + card), product amounts
are split proportionally by the payment fractions.
VAT rate: 19% for all products.
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import logging

from zettle_client import CARD_PAYMENT_TYPES

logger = logging.getLogger(__name__)

VAT_RATE = Decimal("0.19")
SERVICE_PRODUCT_NAMES = {"Dienstleistung", "Beispiel Zusatzleistung"}


@dataclass
class DailySummary:
    # Barzahlung × Dienstleistung
    cash_service_gross: Decimal = field(default_factory=Decimal)
    cash_service_vat:   Decimal = field(default_factory=Decimal)
    cash_service_net:   Decimal = field(default_factory=Decimal)
    # Barzahlung × Warenverkauf
    cash_sales_gross:   Decimal = field(default_factory=Decimal)
    cash_sales_vat:     Decimal = field(default_factory=Decimal)
    cash_sales_net:     Decimal = field(default_factory=Decimal)
    # Kartenzahlung × Dienstleistung
    card_service_gross: Decimal = field(default_factory=Decimal)
    card_service_vat:   Decimal = field(default_factory=Decimal)
    card_service_net:   Decimal = field(default_factory=Decimal)
    # Kartenzahlung × Warenverkauf
    card_sales_gross:   Decimal = field(default_factory=Decimal)
    card_sales_vat:     Decimal = field(default_factory=Decimal)
    card_sales_net:     Decimal = field(default_factory=Decimal)

    # ── Computed totals ───────────────────────────────────────────────────────

    @property
    def cash_gross(self) -> Decimal:
        return self.cash_service_gross + self.cash_sales_gross

    @property
    def card_gross(self) -> Decimal:
        return self.card_service_gross + self.card_sales_gross

    @property
    def service_gross(self) -> Decimal:
        return self.cash_service_gross + self.card_service_gross

    @property
    def sales_gross(self) -> Decimal:
        return self.cash_sales_gross + self.card_sales_gross

    @property
    def total_gross(self) -> Decimal:
        return self.cash_gross + self.card_gross

    @property
    def total_vat(self) -> Decimal:
        return (self.cash_service_vat + self.cash_sales_vat +
                self.card_service_vat + self.card_sales_vat)

    @property
    def total_net(self) -> Decimal:
        return (self.cash_service_net + self.cash_sales_net +
                self.card_service_net + self.card_sales_net)

    @property
    def has_cash(self) -> bool:
        return self.cash_gross > Decimal("0")

    @property
    def has_card(self) -> bool:
        return self.card_gross > Decimal("0")

    def is_empty(self) -> bool:
        return self.total_gross == Decimal("0")


def aggregate(purchases: list[dict]) -> DailySummary:
    """Build a DailySummary from raw Zettle purchases.

    For each purchase, payment amounts determine the cash/card split fractions.
    Product amounts are then distributed proportionally across the 4 buckets.
    """
    summary = DailySummary()
    for purchase in purchases:
        _process_purchase(summary, purchase)
    return summary


# ── Per-purchase logic ────────────────────────────────────────────────────────

def _process_purchase(summary: DailySummary, purchase: dict) -> None:
    # Step 1: cash vs card fractions from payment amounts (in minor units)
    cash_minor = Decimal("0")
    card_minor = Decimal("0")
    for payment in purchase.get("payments", []):
        if not isinstance(payment, dict):
            continue
        amount = Decimal(str(payment.get("amount", 0)))
        if payment.get("type") in CARD_PAYMENT_TYPES:
            card_minor += amount
        else:
            cash_minor += amount

    total_payment = cash_minor + card_minor
    if total_payment == Decimal("0"):
        return

    cash_frac = cash_minor / total_payment
    card_frac = card_minor / total_payment

    # Step 2: product gross amounts split into service vs sales
    for prod in purchase.get("products", []):
        if not isinstance(prod, dict):
            continue
        gross = _product_gross(prod)
        if gross == Decimal("0"):
            continue

        name = prod.get("name", "")
        if name in SERVICE_PRODUCT_NAMES:
            _add(summary, "cash_service", gross, cash_frac)
            _add(summary, "card_service", gross, card_frac)
        else:
            if name not in ("Verkauf",):
                logger.warning("Unbekannter Produktname %r → als Warenverkauf gebucht", name)
            _add(summary, "cash_sales", gross, cash_frac)
            _add(summary, "card_sales", gross, card_frac)


def _add(summary: DailySummary, bucket: str, gross_total: Decimal, frac: Decimal) -> None:
    """Round proportional gross and add it with VAT to the given bucket."""
    gross = (gross_total * frac).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    vat   = _vat_from_gross(gross)
    net   = gross - vat
    setattr(summary, f"{bucket}_gross", getattr(summary, f"{bucket}_gross") + gross)
    setattr(summary, f"{bucket}_vat",   getattr(summary, f"{bucket}_vat")   + vat)
    setattr(summary, f"{bucket}_net",   getattr(summary, f"{bucket}_net")   + net)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _product_gross(prod: dict) -> Decimal:
    try:
        qty        = Decimal(str(prod.get("quantity", "1")))
        unit_minor = Decimal(str(prod.get("unitPrice", 0)))
        return (qty * unit_minor / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _vat_from_gross(gross: Decimal) -> Decimal:
    return (gross * VAT_RATE / (1 + VAT_RATE)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
