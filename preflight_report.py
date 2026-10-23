"""Read-only period preflight before any Lexware Office write."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, DecimalException
import time

from accounting_config import AccountingConfig, ConfigurationError
from aggregator import AggregationError, DailySummary, aggregate
from zettle_client import ZettleAPIError


@dataclass
class PreflightReport:
    start_date: date
    end_date: date
    days_requested: int
    days_with_purchases: int = 0
    empty_days: int = 0
    valid_days: int = 0
    summary: DailySummary = field(default_factory=DailySummary)
    tip_totals_by_role: dict[str, Decimal] = field(default_factory=dict)
    errors: dict[date, str] = field(default_factory=dict)
    unmapped_products: set[str] = field(default_factory=set)
    gift_card_days: set[date] = field(default_factory=set)
    zero_vat_products: set[str] = field(default_factory=set)
    unexpected_vat_rates: set[Decimal] = field(default_factory=set)

    @property
    def tip_total(self) -> Decimal:
        return sum(self.tip_totals_by_role.values(), Decimal("0"))

    @property
    def product_sales_gross(self) -> Decimal:
        return self.summary.total_gross - self.tip_total

    @property
    def blockers(self) -> bool:
        return bool(
            self.errors
            or self.unmapped_products
            or self.gift_card_days
            or self.zero_vat_products
            or self.unexpected_vat_rates
        )


def build_preflight_report(
    days: list[date],
    zettle: object,
    config: AccountingConfig,
    pause_seconds: float = 0,
) -> PreflightReport:
    if not days:
        raise ValueError("Mindestens ein Prüftag ist erforderlich")
    report = PreflightReport(days[0], days[-1], len(days))

    for index, business_date in enumerate(days):
        try:
            purchases = zettle.get_purchases_for_day(business_date)
        except ZettleAPIError as exc:
            report.errors[business_date] = f"Zettle-Fehler: {exc}"
            _pause(index, days, pause_seconds)
            continue

        if not purchases:
            report.empty_days += 1
            _pause(index, days, pause_seconds)
            continue
        report.days_with_purchases += 1
        _observe_risks(report, business_date, purchases, config)

        try:
            daily = aggregate(purchases, config)
            tips = _tip_totals(purchases, config)
        except (AggregationError, ConfigurationError) as exc:
            report.errors[business_date] = f"Buchungsprüfung: {exc}"
            _pause(index, days, pause_seconds)
            continue

        _merge_summary(report.summary, daily)
        for role, amount in tips.items():
            report.tip_totals_by_role[role] = (
                report.tip_totals_by_role.get(role, Decimal("0")) + amount
            )
        report.valid_days += 1
        _pause(index, days, pause_seconds)

    return report


def format_preflight_report(report: PreflightReport) -> str:
    lines = [
        f"Monats-/Zeitraumprüfung {report.start_date} bis {report.end_date}",
        "Nur Zettle gelesen; kein Zugriff auf Lexware Office.",
        (
            f"Tage: {report.days_requested}; mit Vorgängen: "
            f"{report.days_with_purchases}; leer: {report.empty_days}; "
            f"vollständig geprüft: {report.valid_days}; "
            f"fehlerhaft: {len(report.errors)}"
        ),
        "",
        "Summen der vollständig geprüften Tage:",
        f"  Produktumsatz brutto: {_money(report.product_sales_gross)}",
        f"  Trinkgeld gesamt: {_money(report.tip_total)}",
        f"  Einnahmen/Buchungszeilen brutto: {_money(report.summary.total_gross)}",
        f"  darin Umsatzsteuer: {_money(report.summary.total_vat)}",
        f"  Erstattungen brutto: {_money(report.summary.refund_total_gross)}",
        f"  darin korrigierte Umsatzsteuer: {_money(report.summary.refund_total_vat)}",
        f"  Saldo brutto: {_money(report.summary.net_gross)}",
        (
            f"  Bar-Saldo: "
            f"{_money(report.summary.cash_gross - report.summary.refund_cash_gross)}"
        ),
        (
            f"  Karten-Saldo: "
            f"{_money(report.summary.card_gross - report.summary.refund_card_gross)}"
        ),
    ]

    _append_vat_breakdown(lines, report)
    _append_category_breakdown(lines, report)
    if report.tip_totals_by_role:
        lines.append("Trinkgeld nach Rolle:")
        for role, amount in sorted(report.tip_totals_by_role.items()):
            label = "Unternehmer" if role == "entrepreneur" else "Mitarbeiter"
            lines.append(f"  {label}: {_money(amount)}")

    if report.unmapped_products:
        lines.append(
            "Nicht konfigurierte Produkte: "
            + ", ".join(sorted(report.unmapped_products))
        )
    if report.zero_vat_products:
        lines.append(
            "Ungeklärte 0-%-Produkte: "
            + ", ".join(sorted(report.zero_vat_products))
        )
    if report.unexpected_vat_rates:
        rates = ", ".join(f"{rate}%" for rate in sorted(report.unexpected_vat_rates))
        lines.append(f"Unerwartete Steuersätze außerhalb 0/7/19: {rates}")
    if report.gift_card_days:
        dates = ", ".join(str(day) for day in sorted(report.gift_card_days))
        lines.append(f"Gutscheinvorgänge (weiterhin gesperrt): {dates}")
    if report.errors:
        lines.append("Fehlerhafte Tage (nicht in den Summen enthalten):")
        for business_date, message in sorted(report.errors.items()):
            lines.append(f"  {business_date}: {message}")

    lines.append("")
    if report.blockers:
        lines.append("Status: BLOCKIERT — keine Übertragung starten")
    else:
        lines.append(
            "Status: KEINE TECHNISCHEN BLOCKER — Bericht fachlich prüfen"
        )
    return "\n".join(lines)


def _observe_risks(
    report: PreflightReport,
    business_date: date,
    purchases: list[dict],
    config: AccountingConfig,
) -> None:
    for purchase in purchases:
        if not isinstance(purchase, dict):
            continue
        for payment in purchase.get("payments", []):
            if isinstance(payment, dict) and payment.get("type") in {
                "GIFTCARD", "GIFT_CARD"
            }:
                report.gift_card_days.add(business_date)
        for product in purchase.get("products", []):
            if not isinstance(product, dict):
                continue
            if product.get("type") in {"GIFTCARD", "GIFT_CARD"}:
                report.gift_card_days.add(business_date)
            name = product.get("name")
            if isinstance(name, str):
                try:
                    config.rule_for_product(name)
                except ConfigurationError:
                    report.unmapped_products.add(name)
            if "vatPercentage" not in product:
                continue
            try:
                rate = Decimal(str(product["vatPercentage"]))
            except (ValueError, TypeError, DecimalException):
                continue
            if not rate.is_finite():
                continue
            if rate == 0 and isinstance(name, str):
                report.zero_vat_products.add(name)
            if rate not in {Decimal("0"), Decimal("7"), Decimal("19")}:
                report.unexpected_vat_rates.add(rate)


def _tip_totals(
    purchases: list[dict], config: AccountingConfig
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for purchase in purchases:
        if purchase.get("refund") is True:
            continue
        for payment in purchase.get("payments", []):
            if not isinstance(payment, dict) or "gratuityAmount" not in payment:
                continue
            gratuity = Decimal(str(payment["gratuityAmount"]))
            if gratuity == 0:
                continue
            rule = config.tip_rule_for_user(purchase.get("userId"))
            amount = (gratuity / 100).quantize(Decimal("0.01"))
            result[rule.role] = result.get(rule.role, Decimal("0")) + amount
    return result


def _merge_summary(target: DailySummary, source: DailySummary) -> None:
    for line in source.lines.values():
        target.add("cash", line.category, line.vat_rate, line.cash_gross)
        target.add("card", line.category, line.vat_rate, line.card_gross)
    for line in source.refund_lines.values():
        target.add_refund("cash", line.category, line.vat_rate, line.cash_gross)
        target.add_refund("card", line.category, line.vat_rate, line.card_gross)


def _append_vat_breakdown(lines: list[str], report: PreflightReport) -> None:
    rates = sorted({rate for _, rate in report.summary.lines} | {
        rate for _, rate in report.summary.refund_lines
    })
    if not rates:
        return
    lines.append("Nach Steuersatz:")
    for rate in rates:
        sales_gross = sum(
            (line.gross for (_, line_rate), line in report.summary.lines.items()
             if line_rate == rate),
            Decimal("0"),
        )
        sales_vat = sum(
            (line.cash_vat + line.card_vat
             for (_, line_rate), line in report.summary.lines.items()
             if line_rate == rate),
            Decimal("0"),
        )
        refunds_gross = sum(
            (line.gross
             for (_, line_rate), line in report.summary.refund_lines.items()
             if line_rate == rate),
            Decimal("0"),
        )
        refunds_vat = sum(
            (line.cash_vat + line.card_vat
             for (_, line_rate), line in report.summary.refund_lines.items()
             if line_rate == rate),
            Decimal("0"),
        )
        lines.append(
            f"  {rate}%: Einnahmen {_money(sales_gross)} "
            f"(USt {_money(sales_vat)}), Erstattungen {_money(refunds_gross)} "
            f"(USt-Korrektur {_money(refunds_vat)})"
        )


def _append_category_breakdown(lines: list[str], report: PreflightReport) -> None:
    sales = report.summary.category_totals()
    refunds = report.summary.refund_category_totals()
    categories = sorted(set(sales) | set(refunds))
    if not categories:
        return
    lines.append("Nach Buchungskategorie:")
    for category in categories:
        lines.append(
            f"  {category}: Einnahmen {_money(sales.get(category, Decimal('0')))}, "
            f"Erstattungen {_money(refunds.get(category, Decimal('0')))}"
        )


def _money(value: Decimal) -> str:
    return f"{value:.2f} €"


def _pause(index: int, days: list[date], seconds: float) -> None:
    if seconds > 0 and index < len(days) - 1:
        time.sleep(seconds)
