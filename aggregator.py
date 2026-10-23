"""Aggregate Zettle purchases by payment method, category and VAT rate."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, ROUND_HALF_UP

from accounting_config import AccountingConfig, ConfigurationError, DEFAULT_CONFIG
from zettle_client import CARD_PAYMENT_TYPES, CASH_PAYMENT_TYPES

VAT_RATE = Decimal("0.19")
SERVICE_PRODUCT_NAMES = set(DEFAULT_CONFIG.product_rules[0].names)
SALES_PRODUCT_NAMES = set(DEFAULT_CONFIG.product_rules[1].names)
CENT = Decimal("0.01")


class AggregationError(ValueError):
    """Raised when a purchase cannot be booked without making assumptions."""


@dataclass
class SummaryLine:
    category: str
    vat_rate: Decimal
    cash_gross: Decimal = field(default_factory=Decimal)
    card_gross: Decimal = field(default_factory=Decimal)

    @property
    def gross(self) -> Decimal:
        return self.cash_gross + self.card_gross

    @property
    def cash_vat(self) -> Decimal:
        return _vat_from_gross(self.cash_gross, self.vat_rate)

    @property
    def card_vat(self) -> Decimal:
        return _vat_from_gross(self.card_gross, self.vat_rate)


@dataclass
class DailySummary:
    lines: dict[tuple[str, Decimal], SummaryLine] = field(default_factory=dict)

    def add(
        self,
        payment_method: str,
        category: str,
        vat_rate: Decimal,
        gross: Decimal,
    ) -> None:
        if gross == 0:
            return
        key = (category, vat_rate)
        line = self.lines.setdefault(key, SummaryLine(category, vat_rate))
        if payment_method == "cash":
            line.cash_gross += gross
        elif payment_method == "card":
            line.card_gross += gross
        else:
            raise AggregationError(f"Unbekannte Zahlungsgruppe: {payment_method!r}")

    def items_for_payment(self, payment_method: str) -> list[SummaryLine]:
        if payment_method not in {"cash", "card"}:
            raise AggregationError(f"Unbekannte Zahlungsgruppe: {payment_method!r}")
        attribute = f"{payment_method}_gross"
        return [line for line in self.lines.values() if getattr(line, attribute) != 0]

    def category_gross(self, category: str) -> Decimal:
        return sum(
            (line.gross for line in self.lines.values() if line.category == category),
            Decimal("0"),
        )

    def category_totals(self) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for line in self.lines.values():
            result[line.category] = result.get(line.category, Decimal("0")) + line.gross
        return result

    @property
    def cash_gross(self) -> Decimal:
        return sum((line.cash_gross for line in self.lines.values()), Decimal("0"))

    @property
    def card_gross(self) -> Decimal:
        return sum((line.card_gross for line in self.lines.values()), Decimal("0"))

    @property
    def total_gross(self) -> Decimal:
        return self.cash_gross + self.card_gross

    @property
    def total_vat(self) -> Decimal:
        return sum(
            (line.cash_vat + line.card_vat for line in self.lines.values()),
            Decimal("0"),
        )

    @property
    def total_net(self) -> Decimal:
        return self.total_gross - self.total_vat

    @property
    def has_cash(self) -> bool:
        return self.cash_gross > 0

    @property
    def has_card(self) -> bool:
        return self.card_gross > 0

    def is_empty(self) -> bool:
        return self.total_gross == 0

    @property
    def service_gross(self) -> Decimal:
        return self.category_gross("service")

    @property
    def sales_gross(self) -> Decimal:
        return self.category_gross("sales")

    @property
    def cash_service_gross(self) -> Decimal:
        return self._legacy_amount("service", "cash_gross")

    @property
    def card_service_gross(self) -> Decimal:
        return self._legacy_amount("service", "card_gross")

    @property
    def cash_sales_gross(self) -> Decimal:
        return self._legacy_amount("sales", "cash_gross")

    @property
    def card_sales_gross(self) -> Decimal:
        return self._legacy_amount("sales", "card_gross")

    @property
    def cash_service_vat(self) -> Decimal:
        return self._legacy_amount("service", "cash_vat")

    @property
    def card_service_vat(self) -> Decimal:
        return self._legacy_amount("service", "card_vat")

    @property
    def cash_sales_vat(self) -> Decimal:
        return self._legacy_amount("sales", "cash_vat")

    @property
    def card_sales_vat(self) -> Decimal:
        return self._legacy_amount("sales", "card_vat")

    @property
    def cash_service_net(self) -> Decimal:
        return self.cash_service_gross - self.cash_service_vat

    @property
    def card_service_net(self) -> Decimal:
        return self.card_service_gross - self.card_service_vat

    @property
    def cash_sales_net(self) -> Decimal:
        return self.cash_sales_gross - self.cash_sales_vat

    @property
    def card_sales_net(self) -> Decimal:
        return self.card_sales_gross - self.card_sales_vat

    def _legacy_amount(self, category: str, attribute: str) -> Decimal:
        return sum(
            (
                getattr(line, attribute)
                for line in self.lines.values()
                if line.category == category
            ),
            Decimal("0"),
        )


def aggregate(
    purchases: list[dict], config: AccountingConfig = DEFAULT_CONFIG
) -> DailySummary:
    """Build a validated daily summary using explicit product rules."""
    summary = DailySummary()
    for purchase in purchases:
        _process_purchase(summary, purchase, config)
    return summary


def _process_purchase(
    summary: DailySummary, purchase: dict, config: AccountingConfig
) -> None:
    if not isinstance(purchase, dict):
        raise AggregationError("Ungültiger Zettle-Kauf: Objekt erwartet")
    if purchase.get("currency") != config.currency:
        raise AggregationError(f"Nur Zettle-Käufe in {config.currency} werden unterstützt")
    if purchase.get("refund") is True:
        raise AggregationError(
            "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
        )

    cash_minor = Decimal("0")
    card_minor = Decimal("0")
    payments = purchase.get("payments", [])
    if not isinstance(payments, list):
        raise AggregationError("Ungültige Zahlungsliste im Zettle-Kauf")
    for payment in payments:
        if not isinstance(payment, dict):
            raise AggregationError("Ungültige Zahlungsposition im Zettle-Kauf")
        amount = _minor_amount(payment, "amount", "Zahlungsbetrag")
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
            raise AggregationError(f"Unbekannter Zettle-Zahlungstyp: {payment_type!r}")

    total_payment_minor = cash_minor + card_minor
    if total_payment_minor == 0:
        if purchase.get("products"):
            raise AggregationError("Zettle-Kauf enthält Produkte, aber keine Zahlung")
        return

    category_totals: dict[tuple[str, Decimal], Decimal] = {}
    products = purchase.get("products", [])
    if not isinstance(products, list):
        raise AggregationError("Ungültige Produktliste im Zettle-Kauf")
    for product in products:
        if not isinstance(product, dict):
            raise AggregationError("Ungültige Produktposition im Zettle-Kauf")
        name = product.get("name")
        if not isinstance(name, str):
            raise AggregationError("Zettle-Produkt ohne gültigen Namen")
        try:
            rule = config.rule_for_product(name)
        except ConfigurationError as exc:
            raise AggregationError(str(exc)) from exc
        _validate_product_tax(product, rule.vat_rate)
        gross = _product_gross(product, allow_discounts=config.allow_discounts)
        if gross == 0:
            continue
        key = (rule.category, rule.vat_rate)
        category_totals[key] = category_totals.get(key, Decimal("0")) + gross

    payment_gross = (total_payment_minor / 100).quantize(CENT)
    product_gross = sum(category_totals.values(), Decimal("0"))
    if product_gross != payment_gross:
        raise AggregationError(
            "Zahlungs- und Produktsumme stimmen nicht überein; "
            "Trinkgeld, Gutschein oder Gebühren sind nicht konfiguriert"
        )

    _allocate_balanced(
        summary,
        category_totals,
        (cash_minor / 100).quantize(CENT),
        payment_gross,
    )


def _allocate_balanced(
    summary: DailySummary,
    category_totals: dict[tuple[str, Decimal], Decimal],
    cash_gross: Decimal,
    total_gross: Decimal,
) -> None:
    """Allocate any number of category/VAT rows while preserving all totals."""
    rows = list(category_totals.items())
    cash_remaining = cash_gross
    row_total_remaining = total_gross
    for index, ((category, vat_rate), row_gross) in enumerate(rows):
        if index == len(rows) - 1:
            cash_row = cash_remaining
        else:
            ideal = (row_gross * cash_gross / total_gross).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
            remaining_after_row = row_total_remaining - row_gross
            lower = max(Decimal("0"), cash_remaining - remaining_after_row)
            upper = min(row_gross, cash_remaining)
            cash_row = min(max(ideal, lower), upper)
        card_row = row_gross - cash_row
        summary.add("cash", category, vat_rate, cash_row)
        summary.add("card", category, vat_rate, card_row)
        cash_remaining -= cash_row
        row_total_remaining -= row_gross


def _minor_amount(source: dict, key: str, label: str) -> Decimal:
    try:
        amount = Decimal(str(source[key]))
    except (KeyError, ValueError, TypeError, DecimalException) as exc:
        raise AggregationError(f"Ungültiger {label}") from exc
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise AggregationError(f"{label} muss eine ganze Anzahl Cent sein")
    return amount


def _product_gross(product: dict, allow_discounts: bool = False) -> Decimal:
    try:
        quantity = Decimal(str(product.get("quantity", "1")))
        unit_minor = Decimal(str(product["unitPrice"]))
    except (KeyError, ValueError, TypeError, DecimalException) as exc:
        raise AggregationError("Ungültiger Produktbetrag im Zettle-Kauf") from exc
    if not quantity.is_finite() or not unit_minor.is_finite():
        raise AggregationError("Produktbetrag muss eine endliche Zahl sein")
    if quantity < 0 or unit_minor < 0:
        raise AggregationError(
            "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
        )
    calculated = (quantity * unit_minor / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    if "grossValue" not in product:
        return calculated

    reported = _minor_amount(product, "grossValue", "Zettle-Bruttowert")
    reported_gross = (reported / 100).quantize(CENT)
    if reported_gross != calculated and not allow_discounts:
        raise AggregationError("Zettle-Produkt enthält einen nicht erlaubten Rabatt")
    if reported_gross < 0:
        raise AggregationError(
            "Rückerstattungen/Stornierungen werden noch nicht unterstützt"
        )
    return reported_gross


def _validate_product_tax(product: dict, configured_rate: Decimal) -> None:
    if product.get("taxExempt") is True and configured_rate != 0:
        raise AggregationError("Steuerbefreites Produkt passt nicht zur Konfiguration")
    if "vatPercentage" not in product:
        return
    try:
        reported_rate = Decimal(str(product["vatPercentage"]))
    except (ValueError, TypeError, DecimalException) as exc:
        raise AggregationError("Ungültiger Zettle-Steuersatz") from exc
    if not reported_rate.is_finite() or reported_rate != configured_rate:
        raise AggregationError(
            f"Zettle-Steuersatz {reported_rate}% passt nicht zur "
            f"Konfiguration ({configured_rate}%)"
        )


def _vat_from_gross(gross: Decimal, vat_rate: Decimal = Decimal("19")) -> Decimal:
    if gross == 0 or vat_rate == 0:
        return Decimal("0.00")
    rate = vat_rate / 100
    return (gross * rate / (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
