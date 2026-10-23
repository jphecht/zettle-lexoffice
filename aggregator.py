"""Aggregates Zettle purchases into a daily summary split by payment method × product type.

Each purchase amount is split into:
  - cash × service   (Barzahlung × Dienstleistung)
  - cash × sales     (Barzahlung × Warenverkauf)
  - card × service   (Kartenzahlung × Dienstleistung)
  - card × sales     (Kartenzahlung × Warenverkauf)

For purchases with mixed payment (partially cash + card), the four buckets
are balanced so both payment totals and product-type totals remain exact.
VAT rate: 19% for all products.
"""
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, ROUND_HALF_UP

from zettle_client import CARD_PAYMENT_TYPES, CASH_PAYMENT_TYPES

VAT_RATE = Decimal("0.19")
SERVICE_PRODUCT_NAMES = {"Dienstleistung", "Beispiel Zusatzleistung"}
SALES_PRODUCT_NAMES = {"Verkauf"}


class AggregationError(ValueError):
    """Raised when a purchase cannot be booked without making assumptions."""


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

    Unknown payment/product types and unreconciled totals are rejected instead
    of being silently assigned to a potentially wrong accounting category.
    """
    summary = DailySummary()
    for purchase in purchases:
        _process_purchase(summary, purchase)
    return summary


# ── Per-purchase logic ────────────────────────────────────────────────────────

def _process_purchase(summary: DailySummary, purchase: dict) -> None:
    if not isinstance(purchase, dict):
        raise AggregationError("Ungültiger Zettle-Kauf: Objekt erwartet")
    if purchase.get("currency") != "EUR":
        raise AggregationError("Nur Zettle-Käufe in EUR werden unterstützt")
    if purchase.get("refund") is True:
        raise AggregationError(
            "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
        )

    # Step 1: cash vs card totals from payment amounts (in minor units)
    cash_minor = Decimal("0")
    card_minor = Decimal("0")
    payments = purchase.get("payments", [])
    if not isinstance(payments, list):
        raise AggregationError("Ungültige Zahlungsliste im Zettle-Kauf")
    for payment in payments:
        if not isinstance(payment, dict):
            raise AggregationError("Ungültige Zahlungsposition im Zettle-Kauf")
        try:
            amount = Decimal(str(payment["amount"]))
        except (KeyError, ValueError, TypeError, DecimalException) as exc:
            raise AggregationError("Ungültiger Zahlungsbetrag im Zettle-Kauf") from exc
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise AggregationError("Zahlungsbetrag muss eine ganze Anzahl Cent sein")
        if amount < 0:
            raise AggregationError(
                "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
            )
        payment_type = payment.get("type")
        if payment_type in CARD_PAYMENT_TYPES:
            card_minor += amount
        elif payment_type in CASH_PAYMENT_TYPES:
            cash_minor += amount
        else:
            raise AggregationError(
                f"Unbekannter Zettle-Zahlungstyp: {payment_type!r}"
            )

    total_payment_minor = cash_minor + card_minor
    if total_payment_minor == Decimal("0"):
        if purchase.get("products"):
            raise AggregationError("Zettle-Kauf enthält Produkte, aber keine Zahlung")
        return

    # Step 2: exact product totals for service vs sales.
    service_gross = Decimal("0")
    sales_gross = Decimal("0")
    products = purchase.get("products", [])
    if not isinstance(products, list):
        raise AggregationError("Ungültige Produktliste im Zettle-Kauf")
    for prod in products:
        if not isinstance(prod, dict):
            raise AggregationError("Ungültige Produktposition im Zettle-Kauf")
        _validate_product_tax(prod)
        gross = _product_gross(prod)
        if gross == Decimal("0"):
            continue

        name = prod.get("name", "")
        if name in SERVICE_PRODUCT_NAMES:
            service_gross += gross
        elif name in SALES_PRODUCT_NAMES:
            sales_gross += gross
        else:
            raise AggregationError(f"Unbekannter Zettle-Produktname: {name!r}")

    payment_gross = (total_payment_minor / 100).quantize(Decimal("0.01"))
    product_gross = service_gross + sales_gross
    if product_gross != payment_gross:
        raise AggregationError(
            "Zahlungs- und Produktsumme stimmen nicht überein; "
            "Trinkgeld, Rabatt, Gutschein oder Gebühren sind nicht unterstützt"
        )

    # Balanced 2×2 allocation: exact payment and product-type totals survive
    # the cent rounding required for mixed payments.
    cash_gross = (cash_minor / 100).quantize(Decimal("0.01"))
    ideal_cash_service = service_gross * cash_gross / product_gross
    cash_service = ideal_cash_service.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    lower_bound = max(Decimal("0"), cash_gross - sales_gross)
    upper_bound = min(service_gross, cash_gross)
    cash_service = min(max(cash_service, lower_bound), upper_bound)

    allocations = {
        "cash_service": cash_service,
        "cash_sales": cash_gross - cash_service,
        "card_service": service_gross - cash_service,
        "card_sales": sales_gross - (cash_gross - cash_service),
    }
    for bucket, gross in allocations.items():
        _add(summary, bucket, gross)


def _add(summary: DailySummary, bucket: str, gross: Decimal) -> None:
    """Add an exact gross amount with its VAT to the given bucket."""
    if gross == Decimal("0"):
        return
    vat   = _vat_from_gross(gross)
    net   = gross - vat
    setattr(summary, f"{bucket}_gross", getattr(summary, f"{bucket}_gross") + gross)
    setattr(summary, f"{bucket}_vat",   getattr(summary, f"{bucket}_vat")   + vat)
    setattr(summary, f"{bucket}_net",   getattr(summary, f"{bucket}_net")   + net)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _product_gross(prod: dict) -> Decimal:
    try:
        qty        = Decimal(str(prod.get("quantity", "1")))
        unit_minor = Decimal(str(prod["unitPrice"]))
    except (KeyError, ValueError, TypeError, DecimalException) as exc:
        raise AggregationError("Ungültiger Produktbetrag im Zettle-Kauf") from exc
    if not qty.is_finite() or not unit_minor.is_finite():
        raise AggregationError("Produktbetrag muss eine endliche Zahl sein")
    if qty < 0 or unit_minor < 0:
        raise AggregationError(
            "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
        )
    gross = (qty * unit_minor / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if "grossValue" in prod:
        try:
            reported_gross = (Decimal(str(prod["grossValue"])) / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (ValueError, TypeError, DecimalException) as exc:
            raise AggregationError("Ungültiger Zettle-Bruttowert") from exc
        if not reported_gross.is_finite() or reported_gross != gross:
            raise AggregationError(
                "Zettle-Produkt enthält Rabatt oder abweichenden Bruttowert"
            )
    return gross


def _validate_product_tax(prod: dict) -> None:
    """Reject explicit tax information that is incompatible with 19% VAT."""
    if prod.get("taxExempt") is True:
        raise AggregationError("Steuerbefreite Produkte werden nicht unterstützt")
    if "vatPercentage" not in prod:
        return
    try:
        rate = Decimal(str(prod["vatPercentage"]))
    except (ValueError, TypeError, DecimalException) as exc:
        raise AggregationError("Ungültiger Zettle-Steuersatz") from exc
    if not rate.is_finite() or rate != Decimal("19"):
        raise AggregationError(
            f"Nicht unterstützter Zettle-Steuersatz: {rate}%"
        )


def _vat_from_gross(gross: Decimal) -> Decimal:
    return (gross * VAT_RATE / (1 + VAT_RATE)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
