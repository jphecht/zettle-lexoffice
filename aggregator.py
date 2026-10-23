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
    refund_lines: dict[tuple[str, Decimal], SummaryLine] = field(default_factory=dict)

    def add(
        self,
        payment_method: str,
        category: str,
        vat_rate: Decimal,
        gross: Decimal,
    ) -> None:
        self._add_to(self.lines, payment_method, category, vat_rate, gross)

    def add_refund(
        self,
        payment_method: str,
        category: str,
        vat_rate: Decimal,
        gross: Decimal,
    ) -> None:
        self._add_to(self.refund_lines, payment_method, category, vat_rate, gross)

    @staticmethod
    def _add_to(
        lines: dict[tuple[str, Decimal], SummaryLine],
        payment_method: str,
        category: str,
        vat_rate: Decimal,
        gross: Decimal,
    ) -> None:
        if gross == 0:
            return
        if gross < 0:
            raise AggregationError("Zusammenfassungsbeträge dürfen nicht negativ sein")
        key = (category, vat_rate)
        line = lines.setdefault(key, SummaryLine(category, vat_rate))
        if payment_method == "cash":
            line.cash_gross += gross
        elif payment_method == "card":
            line.card_gross += gross
        else:
            raise AggregationError(f"Unbekannte Zahlungsgruppe: {payment_method!r}")

    def items_for_payment(self, payment_method: str) -> list[SummaryLine]:
        return self._items_for_payment(self.lines, payment_method)

    def refund_items_for_payment(self, payment_method: str) -> list[SummaryLine]:
        return self._items_for_payment(self.refund_lines, payment_method)

    @staticmethod
    def _items_for_payment(
        lines: dict[tuple[str, Decimal], SummaryLine], payment_method: str
    ) -> list[SummaryLine]:
        if payment_method not in {"cash", "card"}:
            raise AggregationError(f"Unbekannte Zahlungsgruppe: {payment_method!r}")
        attribute = f"{payment_method}_gross"
        return [line for line in lines.values() if getattr(line, attribute) != 0]

    def category_gross(self, category: str) -> Decimal:
        return sum(
            (line.gross for line in self.lines.values() if line.category == category),
            Decimal("0"),
        )

    def category_totals(self) -> dict[str, Decimal]:
        return self._category_totals(self.lines)

    def refund_category_totals(self) -> dict[str, Decimal]:
        return self._category_totals(self.refund_lines)

    @staticmethod
    def _category_totals(
        lines: dict[tuple[str, Decimal], SummaryLine]
    ) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for line in lines.values():
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
    def refund_cash_gross(self) -> Decimal:
        return sum(
            (line.cash_gross for line in self.refund_lines.values()), Decimal("0")
        )

    @property
    def refund_card_gross(self) -> Decimal:
        return sum(
            (line.card_gross for line in self.refund_lines.values()), Decimal("0")
        )

    @property
    def refund_total_gross(self) -> Decimal:
        return self.refund_cash_gross + self.refund_card_gross

    @property
    def refund_total_vat(self) -> Decimal:
        return sum(
            (line.cash_vat + line.card_vat for line in self.refund_lines.values()),
            Decimal("0"),
        )

    @property
    def net_gross(self) -> Decimal:
        return self.total_gross - self.refund_total_gross

    @property
    def has_cash(self) -> bool:
        return self.cash_gross > 0

    @property
    def has_card(self) -> bool:
        return self.card_gross > 0

    @property
    def has_cash_refund(self) -> bool:
        return self.refund_cash_gross > 0

    @property
    def has_card_refund(self) -> bool:
        return self.refund_card_gross > 0

    def is_empty(self) -> bool:
        return self.total_gross == 0 and self.refund_total_gross == 0

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
    is_refund = purchase.get("refund") is True
    if is_refund and config.refund_mode != "separateCreditNote":
        raise AggregationError(
            "Rückerstattungen sind nicht konfiguriert"
        )
    if is_refund:
        reference = (
            purchase.get("refundsPurchaseUUID1")
            or purchase.get("refundsPurchaseUUID")
        )
        if not isinstance(reference, str) or not reference.strip():
            raise AggregationError("Zettle-Rückerstattung ohne Ursprungskauf-Referenz")

    cash_minor = Decimal("0")
    card_minor = Decimal("0")
    cash_tip_minor = Decimal("0")
    card_tip_minor = Decimal("0")
    raw_payment_total_minor = Decimal("0")
    payments = purchase.get("payments", [])
    if not isinstance(payments, list):
        raise AggregationError("Ungültige Zahlungsliste im Zettle-Kauf")
    for payment in payments:
        if not isinstance(payment, dict):
            raise AggregationError("Ungültige Zahlungsposition im Zettle-Kauf")
        amount = _minor_amount(payment, "amount", "Zahlungsbetrag")
        raw_payment_total_minor += amount
        gratuity = (
            _minor_amount(payment, "gratuityAmount", "Trinkgeldbetrag")
            if "gratuityAmount" in payment
            else Decimal("0")
        )
        if is_refund:
            if amount >= 0:
                raise AggregationError(
                    "Zettle-Rückerstattung benötigt negative Zahlungsbeträge"
                )
            references = payment.get("references")
            refunded_payment = (
                references.get("refundsPayment")
                if isinstance(references, dict)
                else None
            )
            if not isinstance(refunded_payment, str) or not refunded_payment.strip():
                raise AggregationError(
                    "Zettle-Rückerstattungszahlung ohne Ursprungsreferenz"
                )
            if gratuity != 0:
                raise AggregationError(
                    "Erstattetes Trinkgeld wird noch nicht unterstützt"
                )
            amount = -amount
            gratuity = Decimal("0")
        else:
            if amount < 0:
                raise AggregationError(
                    "Negative Zahlung ohne Zettle-Rückerstattungskennzeichen"
                )
            if gratuity < 0 or gratuity > amount:
                raise AggregationError("Ungültiger Zettle-Trinkgeldbetrag")
            if gratuity and config.tip_mode != "byCashier":
                raise AggregationError("Trinkgeld ist nicht konfiguriert")
        payment_type = payment.get("type")
        if payment_type in {"GIFTCARD", "GIFT_CARD"}:
            raise AggregationError(
                _gift_card_block_message(config, "Gutscheinzahlung")
            )
        if payment_type in CARD_PAYMENT_TYPES:
            card_minor += amount - gratuity
            card_tip_minor += gratuity
        elif payment_type in CASH_PAYMENT_TYPES:
            cash_minor += amount - gratuity
            cash_tip_minor += gratuity
        else:
            raise AggregationError(f"Unbekannter Zettle-Zahlungstyp: {payment_type!r}")

    total_payment_minor = cash_minor + card_minor
    total_tip_minor = cash_tip_minor + card_tip_minor
    if "amount" in purchase:
        declared_amount = _minor_amount(purchase, "amount", "Zettle-Kaufsumme")
        allowed_declared_amounts = {raw_payment_total_minor}
        if not is_refund:
            # Zettle documents that payment.amount includes gratuity. Depending
            # on purchase source, purchase.amount may describe the receipt
            # either with or without that non-product amount.
            allowed_declared_amounts.add(raw_payment_total_minor - total_tip_minor)
        if declared_amount not in allowed_declared_amounts:
            raise AggregationError(
                "Zettle-Kaufsumme stimmt nicht mit den Zahlungen überein"
            )
        if is_refund and declared_amount >= 0:
            raise AggregationError("Zettle-Rückerstattung benötigt eine negative Kaufsumme")
        if not is_refund and declared_amount < 0:
            raise AggregationError(
                "Negative Kaufsumme ohne Zettle-Rückerstattungskennzeichen"
            )
    if total_payment_minor == 0 and total_tip_minor == 0:
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
        if product.get("type") == "GIFTCARD":
            raise AggregationError(
                _gift_card_block_message(config, "Gutscheinverkauf")
            )
        name = product.get("name")
        if not isinstance(name, str):
            raise AggregationError("Zettle-Produkt ohne gültigen Namen")
        try:
            rule = config.rule_for_product(name)
        except ConfigurationError as exc:
            raise AggregationError(str(exc)) from exc
        _validate_product_tax(product, rule.vat_rate)
        gross = _product_gross(
            product,
            allow_discounts=config.allow_discounts,
            refund=is_refund,
        )
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
        refund=is_refund,
    )

    if total_tip_minor:
        try:
            tip_rule = config.tip_rule_for_user(purchase.get("userId"))
        except ConfigurationError as exc:
            raise AggregationError(str(exc)) from exc
        summary.add(
            "cash",
            tip_rule.category,
            tip_rule.vat_rate,
            (cash_tip_minor / 100).quantize(CENT),
        )
        summary.add(
            "card",
            tip_rule.category,
            tip_rule.vat_rate,
            (card_tip_minor / 100).quantize(CENT),
        )


def _gift_card_block_message(config: AccountingConfig, event: str) -> str:
    recommendation = config.gift_cards.recommendation
    if recommendation == "singlePurposeCandidate":
        review = "Vorprüfung: Einzweckgutschein-Kandidat"
    elif recommendation == "multiPurposeCandidate":
        review = "Vorprüfung: Mehrzweckgutschein-Kandidat"
    else:
        review = "keine fachliche Gutscheinprüfung konfiguriert"
    return f"Zettle-{event} ist gesperrt ({review})"


def _allocate_balanced(
    summary: DailySummary,
    category_totals: dict[tuple[str, Decimal], Decimal],
    cash_gross: Decimal,
    total_gross: Decimal,
    refund: bool = False,
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
        add = summary.add_refund if refund else summary.add
        add("cash", category, vat_rate, cash_row)
        add("card", category, vat_rate, card_row)
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


def _product_gross(
    product: dict, allow_discounts: bool = False, refund: bool = False
) -> Decimal:
    try:
        quantity = Decimal(str(product.get("quantity", "1")))
        unit_minor = Decimal(str(product["unitPrice"]))
    except (KeyError, ValueError, TypeError, DecimalException) as exc:
        raise AggregationError("Ungültiger Produktbetrag im Zettle-Kauf") from exc
    if not quantity.is_finite() or not unit_minor.is_finite():
        raise AggregationError("Produktbetrag muss eine endliche Zahl sein")
    if unit_minor < 0:
        raise AggregationError("Zettle-Produktpreis darf nicht negativ sein")
    if refund:
        if quantity >= 0:
            raise AggregationError(
                "Zettle-Rückerstattung benötigt negative Produktmengen"
            )
        calculated = (-quantity * unit_minor / 100).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    else:
        if quantity < 0:
            raise AggregationError(
                "Negative Produktmenge ohne Zettle-Rückerstattungskennzeichen"
            )
        calculated = (quantity * unit_minor / 100).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    if "grossValue" not in product:
        return calculated

    reported = _minor_amount(product, "grossValue", "Zettle-Bruttowert")
    if refund and reported > 0:
        raise AggregationError(
            "Zettle-Rückerstattung benötigt einen negativen Bruttowert"
        )
    if not refund and reported < 0:
        raise AggregationError(
            "Negativer Bruttowert ohne Zettle-Rückerstattungskennzeichen"
        )
    reported_gross = (abs(reported) / 100).quantize(CENT)
    if reported_gross != calculated and not allow_discounts:
        raise AggregationError("Zettle-Produkt enthält einen nicht erlaubten Rabatt")
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
