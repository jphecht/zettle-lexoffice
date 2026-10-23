"""Read-only review of a Zettle product library against accounting rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DecimalException

from accounting_config import AccountingConfig, ConfigurationError


@dataclass
class CatalogReview:
    product_count: int = 0
    variant_count: int = 0
    vat_rates: set[Decimal] = field(default_factory=set)
    unmapped_products: set[str] = field(default_factory=set)
    rate_conflicts: set[str] = field(default_factory=set)
    invalid_products: set[str] = field(default_factory=set)
    gift_card_products: set[str] = field(default_factory=set)
    zero_rate_products: set[str] = field(default_factory=set)
    configured_but_missing: set[str] = field(default_factory=set)
    product_vat_rates: dict[str, frozenset[Decimal]] = field(default_factory=dict)
    redeemable_vat_rates: set[Decimal] = field(default_factory=set)
    gift_card_recommendation: str | None = None

    @property
    def blockers(self) -> bool:
        return bool(
            self.unmapped_products
            or self.rate_conflicts
            or self.invalid_products
            or self.gift_card_products
            or self.zero_rate_products
        )


def review_product_catalog(
    products: list[dict], config: AccountingConfig
) -> CatalogReview:
    """Compare product names and variant VAT rates without changing either system."""
    review = CatalogReview(product_count=len(products))
    catalog_names: set[str] = set()

    for index, product in enumerate(products, 1):
        fallback = f"Katalogeintrag {index}"
        if not isinstance(product, dict):
            review.invalid_products.add(fallback)
            continue

        name = product.get("name")
        if not isinstance(name, str) or not name.strip():
            review.invalid_products.add(fallback)
            continue
        name = name.strip()
        catalog_names.add(name)

        variants = product.get("variants")
        product_type = product.get("type")
        variant_types = {
            variant.get("type")
            for variant in variants or []
            if isinstance(variant, dict)
        }
        gift_card_types = {"GIFTCARD", "GIFT_CARD"}
        is_gift_card = (
            product_type in gift_card_types
            or bool(gift_card_types & variant_types)
        )
        if is_gift_card:
            review.gift_card_products.add(name)

        if not isinstance(variants, list) or not variants:
            if not is_gift_card:
                review.invalid_products.add(name)
            continue

        product_rates: set[Decimal] = set()
        valid_variants = 0
        for variant in variants:
            if not isinstance(variant, dict):
                review.invalid_products.add(name)
                continue
            valid_variants += 1
            if is_gift_card:
                continue
            try:
                if (
                    variant.get("taxExempt") is True
                    and variant.get("vatPercentage") is None
                ):
                    rate = Decimal("0")
                else:
                    rate = Decimal(str(variant["vatPercentage"]))
            except (KeyError, ValueError, TypeError, DecimalException):
                review.invalid_products.add(name)
                continue
            if not rate.is_finite() or rate < 0 or rate > 100:
                review.invalid_products.add(name)
                continue
            product_rates.add(rate)
            review.vat_rates.add(rate)
            if rate == 0:
                review.zero_rate_products.add(name)
        review.variant_count += valid_variants
        review.product_vat_rates[name] = frozenset(product_rates)

        if is_gift_card:
            continue
        try:
            rule = config.rule_for_product(name)
        except ConfigurationError:
            review.unmapped_products.add(name)
            continue
        if rule.category in config.gift_cards.redeemable_categories:
            review.redeemable_vat_rates.update(product_rates)
        if not product_rates or product_rates != {rule.vat_rate}:
            review.rate_conflicts.add(name)

    configured_names = {
        name for rule in config.product_rules for name in rule.names
    }
    review.configured_but_missing = configured_names - catalog_names
    if config.gift_cards.mode == "reviewRequired":
        if len(review.redeemable_vat_rates) == 1:
            review.gift_card_recommendation = "singlePurposeCandidate"
        elif len(review.redeemable_vat_rates) > 1:
            review.gift_card_recommendation = "multiPurposeCandidate"
    return review


def format_catalog_review(review: CatalogReview, config: AccountingConfig) -> str:
    """Return a concise German console report without identifiers or secrets."""
    rates = ", ".join(f"{rate}%" for rate in sorted(review.vat_rates)) or "keine"
    lines = [
        "Zettle-Katalogprüfung (nur lesend)",
        f"Produkte: {review.product_count}; Varianten: {review.variant_count}",
        f"Gefundene Steuersätze: {rates}",
    ]

    _append_names(lines, "Nicht konfigurierte Produkte", review.unmapped_products)
    _append_names(lines, "Abweichende/mehrdeutige Steuersätze", review.rate_conflicts)
    _append_names(
        lines, "Ungültige oder unvollständige Produkte", review.invalid_products
    )
    _append_names(
        lines, "Gutscheinprodukte (weiterhin gesperrt)", review.gift_card_products
    )
    _append_names(
        lines, "0-%-Produkte (Steuergrund manuell prüfen)", review.zero_rate_products
    )
    _append_names(
        lines, "Konfiguriert, aber nicht im Katalog", review.configured_but_missing
    )

    if config.gift_cards.industry_profile:
        lines.append(f"Branchenprofil: {config.gift_cards.industry_profile}")
    if review.gift_card_recommendation == "singlePurposeCandidate":
        lines.append("Gutschein-Vorprüfung: Einzweckgutschein-Kandidat")
    elif review.gift_card_recommendation == "multiPurposeCandidate":
        lines.append("Gutschein-Vorprüfung: Mehrzweckgutschein-Kandidat")
    elif config.gift_cards.mode == "reviewRequired":
        lines.append("Gutschein-Vorprüfung: keine ausreichenden Katalogdaten")
    else:
        lines.append("Gutschein-Vorprüfung: nicht konfiguriert")

    if review.blockers:
        lines.append("Status: NICHT IMPORTBEREIT — Konfiguration prüfen")
    else:
        lines.append("Status: Katalogprüfung ohne Blocker")
    return "\n".join(lines)


def _append_names(lines: list[str], label: str, names: set[str]) -> None:
    if names:
        lines.append(f"{label}: " + ", ".join(sorted(names)))
