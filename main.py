#!/usr/bin/env python3
"""
Zettle POS → Lexoffice Tageseinnahmen-Import.

Verwendung:
  python main.py                                    # gestern
  python main.py --date 2025-01-15                  # bestimmter Tag
  python main.py --from 2025-01-01 --to 2025-05-31  # Bulk-Import
  python main.py --from 2025-01-01 --to 2025-05-31 --dry-run
  python main.py --review-gift-cards                 # nur lokale Vorprüfung
  python main.py --review-catalog                    # Zettle nur lesend
"""
import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from accounting_config import (
    AccountingConfig,
    ConfigurationError,
    DEFAULT_CONFIG,
    load_accounting_config,
)
from aggregator import AggregationError, DailySummary, aggregate
from catalog_review import format_catalog_review, review_product_catalog
from lexoffice_client import LexofficeAPIError, LexofficeClient
from zettle_client import ZettleAPIError, ZettleClient

LOG_DIR = Path(__file__).parent / "logs"

STATUS_OK      = "OK"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED  = "FAILED"

logger = logging.getLogger(__name__)


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.log"

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S"
    )
    file_h = logging.FileHandler(log_file, encoding="utf-8")
    file_h.setFormatter(fmt)
    file_h.setLevel(logging.DEBUG)

    console_h = logging.StreamHandler()
    console_h.setFormatter(fmt)
    console_h.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_h)
    root.addHandler(console_h)

    logger.debug("Logdatei: %s", log_file)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Zettle POS Tageseinnahmen nach Lexoffice übertragen"
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Einzelner Tag (default: gestern)",
    )
    group.add_argument(
        "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Startdatum für Bulk-Import",
    )
    group.add_argument(
        "--review-gift-cards", action="store_true",
        help="Gutschein-Konfiguration lokal prüfen (keine API-Aufrufe)",
    )
    group.add_argument(
        "--review-catalog", action="store_true",
        help="Zettle-Produktkatalog nur lesend gegen die Konfiguration prüfen",
    )
    p.add_argument(
        "--to", dest="to_date", metavar="YYYY-MM-DD",
        help="Enddatum für Bulk-Import (erforderlich mit --from)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Kein Schreibzugriff auf Lexoffice — Payload wird nur geloggt",
    )
    args = p.parse_args()

    if args.from_date and not args.to_date:
        p.error("--from erfordert auch --to")
    if args.to_date and not args.from_date:
        p.error("--to erfordert auch --from")
    if (args.review_gift_cards or args.review_catalog) and args.dry_run:
        p.error("Prüfmodi können nicht mit --dry-run kombiniert werden")

    return args


def _to_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        sys.exit(f"Ungültiges Datum: {s!r}  (erwartet: YYYY-MM-DD)")


def _build_date_range(args: argparse.Namespace) -> list[date]:
    if args.from_date:
        start, end = _to_date(args.from_date), _to_date(args.to_date)
        if start > end:
            sys.exit("--from muss vor oder gleich --to liegen")
        days, cur = [], start
        while cur <= end:
            days.append(cur)
            cur += timedelta(days=1)
        return days

    if args.date:
        return [_to_date(args.date)]

    return [date.today() - timedelta(days=1)]


def _gift_card_review_detail(config: AccountingConfig) -> str:
    policy = config.gift_cards
    if policy.mode == "reject":
        return "Gutscheine: gesperrt; keine fachliche Vorprüfung konfiguriert"

    rates = ", ".join(f"{rate}%" for rate in policy.vat_rates)
    categories = ", ".join(sorted(policy.redeemable_categories))
    if policy.recommendation == "singlePurposeCandidate":
        recommendation = "Einzweckgutschein-Kandidat"
    else:
        recommendation = "Mehrzweckgutschein-Kandidat"
    return (
        f"Branche: {policy.industry_profile}; "
        f"einlösbare Kategorien: {categories}; "
        f"Steuersätze: {rates}; Vorprüfung: {recommendation}. "
        "Keine Buchungsfreigabe — Leistungsort und Gutscheinbedingungen "
        "müssen noch bestätigt werden."
    )


# ── Credentials ───────────────────────────────────────────────────────────────

def _load_clients() -> tuple[ZettleClient, LexofficeClient, AccountingConfig]:
    required = {
        "ZETTLE_API_KEY":                 os.environ.get("ZETTLE_API_KEY", ""),
        "LEXOFFICE_API_KEY":              os.environ.get("LEXOFFICE_API_KEY", ""),
        "LEXOFFICE_CONTACT_ID":           os.environ.get("LEXOFFICE_CONTACT_ID", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        sys.exit(
            "Fehlende Umgebungsvariablen in .env:\n"
            + "\n".join(f"  {k}" for k in missing)
        )

    try:
        config = load_accounting_config(os.environ.get("ACCOUNTING_CONFIG"))
        category_ids = config.category_ids()
    except ConfigurationError as exc:
        sys.exit(f"Ungültige Buchungskonfiguration: {exc}")

    zettle = ZettleClient(required["ZETTLE_API_KEY"])
    lexoffice = LexofficeClient(
        required["LEXOFFICE_API_KEY"],
        category_ids,
        required["LEXOFFICE_CONTACT_ID"],
    )
    return zettle, lexoffice, config


# ── Single-day processing ─────────────────────────────────────────────────────

def _process_day(
    business_date: date,
    zettle: ZettleClient,
    lexoffice: LexofficeClient,
    dry_run: bool,
    config: AccountingConfig = DEFAULT_CONFIG,
) -> tuple[str, str]:
    """Process one day. Returns (status, human-readable detail)."""
    try:
        purchases = zettle.get_purchases_for_day(business_date)

        if not purchases:
            return STATUS_SKIPPED, "kein Umsatz"

        summary: DailySummary = aggregate(purchases, config)

        if summary.is_empty():
            return STATUS_SKIPPED, "kein Umsatz"

        created = lexoffice.create_daily_vouchers(
            business_date, summary, dry_run=dry_run
        )

        if not dry_run and len(created) == 0:
            return STATUS_SKIPPED, "Belege bereits vorhanden"

        category_detail = ", ".join(
            f"{category} {gross:.2f} €"
            for category, gross in summary.category_totals().items()
        )
        refund_detail = ", ".join(
            f"{category} {gross:.2f} €"
            for category, gross in summary.refund_category_totals().items()
        )
        detail = (
            f"Umsatz {summary.total_gross:.2f} €"
            f"  Erstattungen {summary.refund_total_gross:.2f} €"
            f"  Netto {summary.net_gross:.2f} €"
            f"  Bar {summary.cash_gross - summary.refund_cash_gross:.2f} €"
            f"  Karte {summary.card_gross - summary.refund_card_gross:.2f} €"
        )
        if category_detail:
            detail += f"  Umsatz-Kategorien ({category_detail})"
        if refund_detail:
            detail += f"  Erstattungs-Kategorien ({refund_detail})"
        if dry_run:
            return STATUS_OK, "[DRY-RUN] " + detail
        return STATUS_OK, detail + f"  ids={created}"

    except ZettleAPIError as exc:
        return STATUS_FAILED, f"Zettle-Fehler: {exc}"
    except AggregationError as exc:
        return STATUS_FAILED, f"Buchungsprüfung fehlgeschlagen: {exc}"
    except LexofficeAPIError as exc:
        return STATUS_FAILED, f"Lexoffice-Fehler: {exc}"
    except Exception as exc:
        logger.exception("Unerwarteter Fehler für %s", business_date)
        return STATUS_FAILED, f"Unerwarteter Fehler: {exc}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()

    args  = _parse_args()
    if args.review_gift_cards:
        try:
            config = load_accounting_config(os.environ.get("ACCOUNTING_CONFIG"))
        except ConfigurationError as exc:
            sys.exit(f"Ungültige Buchungskonfiguration: {exc}")
        print(_gift_card_review_detail(config))
        return

    if args.review_catalog:
        api_key = os.environ.get("ZETTLE_API_KEY", "")
        if not api_key:
            sys.exit("Fehlende Umgebungsvariable in .env: ZETTLE_API_KEY")
        try:
            config = load_accounting_config(os.environ.get("ACCOUNTING_CONFIG"))
            products = ZettleClient(api_key).get_products()
            review = review_product_catalog(products, config)
        except ConfigurationError as exc:
            sys.exit(f"Ungültige Buchungskonfiguration: {exc}")
        except ZettleAPIError as exc:
            sys.exit(f"Zettle-Fehler: {exc}")
        print(format_catalog_review(review, config))
        if review.blockers:
            sys.exit(1)
        return

    _setup_logging()
    days  = _build_date_range(args)
    total = len(days)
    width = len(str(total))

    zettle, lexoffice, config = _load_clients()

    if args.dry_run:
        logger.info("=== DRY-RUN — keine Änderungen in Lexoffice ===")

    counts = {STATUS_OK: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0}

    for i, day in enumerate(days, 1):
        prefix = f"[{i:{width}d}/{total}] {day}"
        status, detail = _process_day(
            day, zettle, lexoffice, dry_run=args.dry_run, config=config
        )
        counts[status] += 1

        if status == STATUS_OK:
            logger.info("%s  ✓  %s", prefix, detail)
        elif status == STATUS_SKIPPED:
            logger.info("%s  –  %s", prefix, detail)
        else:
            logger.error("%s  ✗  %s", prefix, detail)

        if i < total:
            time.sleep(1)

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    logger.info("")
    logger.info("─" * 50)
    logger.info("Zusammenfassung  (%d Tag(e) verarbeitet)", total)
    logger.info("  ✓ Erfolgreich:    %d", counts[STATUS_OK])
    logger.info("  – Übersprungen:   %d", counts[STATUS_SKIPPED])
    logger.info("  ✗ Fehlgeschlagen: %d", counts[STATUS_FAILED])
    logger.info("─" * 50)

    if counts[STATUS_FAILED]:
        sys.exit(1)


if __name__ == "__main__":
    main()
