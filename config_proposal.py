"""Generate a non-activatable accounting configuration proposal."""
from __future__ import annotations

from decimal import Decimal

from accounting_config import AccountingConfig
from catalog_review import CatalogReview, review_product_catalog


def build_config_proposal(
    products: list[dict], config: AccountingConfig
) -> tuple[dict, CatalogReview]:
    """Return a deterministic draft that requires explicit manual confirmation."""
    review = review_product_catalog(products, config)
    categories = {
        key: {"environmentVariable": variable}
        for key, variable in config.category_environment.items()
    }
    product_rules = [
        {
            "names": sorted(rule.names),
            "category": rule.category,
            "vatRate": _rate_text(rule.vat_rate),
        }
        for rule in config.product_rules
    ]

    suggestions: list[dict] = []
    counter = 1
    unresolved = (
        review.invalid_products
        | review.rate_conflicts
        | review.gift_card_products
    )
    for name in sorted(review.unmapped_products - unresolved):
        rates = review.product_vat_rates.get(name, frozenset())
        if len(rates) != 1:
            unresolved.add(name)
            continue
        while f"reviewProduct{counter:03d}" in categories:
            counter += 1
        category = f"reviewProduct{counter:03d}"
        environment_variable = f"REVIEW_REQUIRED_PRODUCT_{counter:03d}_CATEGORY_ID"
        rate = next(iter(rates))
        categories[category] = {"environmentVariable": environment_variable}
        product_rules.append(
            {
                "names": [name],
                "category": category,
                "vatRate": _rate_text(rate),
            }
        )
        suggestions.append(
            {
                "product": name,
                "observedVatRate": _rate_text(rate),
                "placeholderCategory": category,
                "action": "Lexware-Kategorie fachlich zuordnen oder Regel zusammenführen",
            }
        )
        counter += 1

    proposal = {
        "proposalStatus": "reviewRequired",
        "_review": {
            "message": (
                "Nicht direkt verwenden. Platzhalter und Steuerfälle prüfen; "
                "danach proposalStatus ausdrücklich auf confirmed setzen."
            ),
            "suggestedMappings": suggestions,
            "unresolvedProducts": sorted(unresolved),
            "giftCardProducts": sorted(review.gift_card_products),
            "zeroVatProducts": sorted(review.zero_rate_products),
            "configuredButMissing": sorted(review.configured_but_missing),
        },
        "currency": config.currency,
        "categories": categories,
        "products": product_rules,
        "adjustments": _serialize_adjustments(config),
    }
    return proposal, review


def _serialize_adjustments(config: AccountingConfig) -> dict:
    if config.tip_mode == "byCashier":
        tips: object = {
            "mode": "byCashier",
            "cashiers": [
                {
                    **{"userId": rule.user_id},
                    **({"displayName": rule.display_name} if rule.display_name else {}),
                    "role": rule.role,
                    "category": rule.category,
                    "vatRate": _rate_text(rule.vat_rate),
                }
                for rule in config.tip_rules
            ],
        }
    else:
        tips = "reject"

    if config.gift_cards.mode == "reviewRequired":
        gift_cards: object = {
            "mode": "reviewRequired",
            "industryProfile": config.gift_cards.industry_profile,
            "redeemableCategories": sorted(
                config.gift_cards.redeemable_categories
            ),
        }
    else:
        gift_cards = "reject"

    return {
        "discounts": "useGrossValue" if config.allow_discounts else "reject",
        "tips": tips,
        "refunds": config.refund_mode,
        "giftCards": gift_cards,
    }


def _rate_text(rate: Decimal) -> str:
    return format(rate, "f")
