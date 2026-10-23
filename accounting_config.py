"""Validated accounting configuration for product mapping and VAT handling."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
import json
import os
from pathlib import Path
from typing import Mapping, Optional


class ConfigurationError(ValueError):
    """Raised when the accounting configuration is missing or ambiguous."""


@dataclass(frozen=True)
class ProductRule:
    names: frozenset[str]
    category: str
    vat_rate: Decimal


@dataclass(frozen=True)
class TipRule:
    """Accounting treatment for tips collected by one Zettle user."""

    user_id: str
    role: str
    category: str
    vat_rate: Decimal
    display_name: Optional[str] = None


@dataclass(frozen=True)
class AccountingConfig:
    currency: str
    product_rules: tuple[ProductRule, ...]
    category_environment: Mapping[str, str]
    allow_discounts: bool = False
    tip_mode: str = "reject"
    tip_rules: tuple[TipRule, ...] = ()
    refund_mode: str = "reject"

    def rule_for_product(self, name: str) -> ProductRule:
        matches = [rule for rule in self.product_rules if name in rule.names]
        if not matches:
            raise ConfigurationError(f"Kein Mapping für Zettle-Produkt {name!r}")
        if len(matches) > 1:
            raise ConfigurationError(f"Mehrdeutiges Mapping für Zettle-Produkt {name!r}")
        return matches[0]

    def category_ids(self, environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
        values = os.environ if environ is None else environ
        result: dict[str, str] = {}
        missing: list[str] = []
        for category, variable in self.category_environment.items():
            value = values.get(variable, "").strip()
            if value:
                result[category] = value
            else:
                missing.append(variable)
        if missing:
            raise ConfigurationError(
                "Fehlende Kategorie-Umgebungsvariablen: " + ", ".join(sorted(missing))
            )
        return result

    def tip_rule_for_user(self, user_id: object) -> TipRule:
        normalized = _user_id(user_id)
        matches = [rule for rule in self.tip_rules if rule.user_id == normalized]
        if not matches:
            raise ConfigurationError(
                f"Keine Trinkgeldregel für Zettle-Benutzer-ID {normalized!r}"
            )
        return matches[0]


DEFAULT_CONFIG = AccountingConfig(
    currency="EUR",
    product_rules=(
        ProductRule(
            names=frozenset({"Dienstleistung", "Damen WaSchFö"}),
            category="service",
            vat_rate=Decimal("19"),
        ),
        ProductRule(
            names=frozenset({"Verkauf"}),
            category="sales",
            vat_rate=Decimal("19"),
        ),
    ),
    category_environment={
        "service": "LEXOFFICE_SERVICE_CATEGORY_ID",
        "sales": "LEXOFFICE_SALES_CATEGORY_ID",
    },
)


def load_accounting_config(path: Optional[str]) -> AccountingConfig:
    """Load JSON configuration, or return the legacy-compatible default."""
    if not path:
        return DEFAULT_CONFIG
    config_path = Path(path).expanduser()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Konfigurationsdatei nicht lesbar: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Ungültiges JSON in Konfigurationsdatei: Zeile {exc.lineno}"
        ) from exc
    return _parse_config(raw)


def _parse_config(raw: object) -> AccountingConfig:
    if not isinstance(raw, dict):
        raise ConfigurationError("Die Buchungskonfiguration muss ein JSON-Objekt sein")

    currency = raw.get("currency", "EUR")
    if currency != "EUR":
        raise ConfigurationError("Derzeit wird ausschließlich EUR unterstützt")

    categories = raw.get("categories")
    if not isinstance(categories, dict) or not categories:
        raise ConfigurationError("Mindestens eine Kategorie ist erforderlich")
    category_environment: dict[str, str] = {}
    for key, value in categories.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigurationError("Kategorieschlüssel dürfen nicht leer sein")
        if not isinstance(value, dict):
            raise ConfigurationError(f"Kategorie {key!r} muss ein Objekt sein")
        variable = value.get("environmentVariable")
        if not isinstance(variable, str) or not variable.strip():
            raise ConfigurationError(
                f"Kategorie {key!r} benötigt environmentVariable"
            )
        category_environment[key] = variable.strip()

    products = raw.get("products")
    if not isinstance(products, list) or not products:
        raise ConfigurationError("Mindestens eine Produktregel ist erforderlich")
    rules: list[ProductRule] = []
    seen_names: set[str] = set()
    for index, item in enumerate(products, 1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"Produktregel {index} muss ein Objekt sein")
        names = item.get("names")
        category = item.get("category")
        if not isinstance(names, list) or not names or not all(
            isinstance(name, str) and name.strip() for name in names
        ):
            raise ConfigurationError(f"Produktregel {index} benötigt gültige names")
        clean_names = frozenset(name.strip() for name in names)
        duplicates = clean_names & seen_names
        if duplicates:
            raise ConfigurationError(
                "Produktnamen dürfen nur einmal vorkommen: " + ", ".join(sorted(duplicates))
            )
        if not isinstance(category, str) or category not in category_environment:
            raise ConfigurationError(f"Unbekannte Kategorie in Produktregel {index}")
        rate = _decimal_rate(item.get("vatRate"), f"Produktregel {index}")
        rules.append(ProductRule(clean_names, category, rate))
        seen_names.update(clean_names)

    adjustments = raw.get("adjustments", {})
    if not isinstance(adjustments, dict):
        raise ConfigurationError("adjustments muss ein Objekt sein")
    discount_mode = adjustments.get("discounts", "reject")
    if discount_mode not in {"reject", "useGrossValue"}:
        raise ConfigurationError(
            "adjustments.discounts muss reject oder useGrossValue sein"
        )
    allow_discounts = discount_mode == "useGrossValue"
    tip_mode, tip_rules = _parse_tip_rules(
        adjustments.get("tips", "reject"), category_environment
    )
    refund_mode = adjustments.get("refunds", "reject")
    if refund_mode != "reject":
        raise ConfigurationError("Erstattungsmodus ist noch nicht fachlich konfiguriert")

    return AccountingConfig(
        currency=currency,
        product_rules=tuple(rules),
        category_environment=category_environment,
        allow_discounts=allow_discounts,
        tip_mode=tip_mode,
        tip_rules=tip_rules,
        refund_mode=refund_mode,
    )


def _parse_tip_rules(
    value: object, category_environment: Mapping[str, str]
) -> tuple[str, tuple[TipRule, ...]]:
    if value == "reject":
        return "reject", ()
    if not isinstance(value, dict) or value.get("mode") != "byCashier":
        raise ConfigurationError(
            "adjustments.tips muss reject oder ein byCashier-Objekt sein"
        )
    cashiers = value.get("cashiers")
    if not isinstance(cashiers, list) or not cashiers:
        raise ConfigurationError("Trinkgeldmodus byCashier benötigt cashiers")

    rules: list[TipRule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(cashiers, 1):
        if not isinstance(item, dict):
            raise ConfigurationError(f"Trinkgeldregel {index} muss ein Objekt sein")
        user_id = _user_id(item.get("userId"))
        if user_id in seen_ids:
            raise ConfigurationError(f"Doppelte Zettle-Benutzer-ID {user_id!r}")
        role = item.get("role")
        if role not in {"entrepreneur", "employee"}:
            raise ConfigurationError(
                f"Trinkgeldregel {index}: role muss entrepreneur oder employee sein"
            )
        category = item.get("category")
        if not isinstance(category, str) or category not in category_environment:
            raise ConfigurationError(
                f"Trinkgeldregel {index}: unbekannte Kategorie"
            )
        vat_rate = _decimal_rate(item.get("vatRate"), f"Trinkgeldregel {index}")
        if role == "employee" and vat_rate != 0:
            raise ConfigurationError(
                f"Trinkgeldregel {index}: Mitarbeiter-Trinkgeld muss vatRate 0 haben"
            )
        display_name = item.get("displayName")
        if display_name is not None and (
            not isinstance(display_name, str) or not display_name.strip()
        ):
            raise ConfigurationError(
                f"Trinkgeldregel {index}: displayName muss eine nichtleere Zeichenfolge sein"
            )
        rules.append(
            TipRule(
                user_id=user_id,
                role=role,
                category=category,
                vat_rate=vat_rate,
                display_name=display_name.strip() if display_name else None,
            )
        )
        seen_ids.add(user_id)
    return "byCashier", tuple(rules)


def _user_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ConfigurationError("Zettle-Benutzer-ID muss Text oder Ganzzahl sein")
    normalized = str(value).strip()
    if not normalized:
        raise ConfigurationError("Zettle-Benutzer-ID darf nicht leer sein")
    return normalized


def _decimal_rate(value: object, index: object) -> Decimal:
    try:
        rate = Decimal(str(value))
    except (ValueError, TypeError, DecimalException) as exc:
        raise ConfigurationError(f"Ungültiger vatRate in {index}") from exc
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ConfigurationError(f"vatRate außerhalb 0 bis 100 in {index}")
    return rate
