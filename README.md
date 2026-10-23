# Zettle → Lexware Office

Überträgt Tageseinnahmen aus Zettle (PayPal POS) als Sammelbelege nach
Lexware Office. Das Projekt ist unabhängig und nicht mit PayPal oder Lexware
verbunden.

**Was das Script macht:**
- Ruft alle Kassenvorgänge eines Tages über die Zettle Purchase API ab
- Trennt Kartenzahlungen und Barzahlungen
- Rechnet konfigurierte Umsatzsteuersätze aus Bruttobeträgen heraus
- Legt je einen Bar- und Karten-Sammelbeleg (`salesinvoice`) in Lexware Office an
- Schützt vor Doppel-Import und überspringt umsatzlose Tage
- Bricht bei unbekannten oder nicht abgestimmten Daten ab, statt sie zu schätzen

> **Wichtige Einschränkung:** Ohne eigene Konfiguration bleibt das bisherige
> Verhalten mit `Dienstleistung`, `Damen WaSchFö`, `Verkauf` und 19 % erhalten.
> Eine eigene JSON-Datei kann Namen, Kategorien, Steuersätze und Rabatte
> konfigurieren. Trinkgeld, Gutscheine, Gebühren, Stornos und Rückerstattungen
> bleiben zunächst gesperrt. Erkennt das Programm eine nicht konfigurierte
> Abweichung, legt es keinen Beleg an.

---

## Voraussetzungen

- Python 3.11 oder neuer
- Ein aktiver Zettle-Account (PayPal POS)
- Ein aktiver Lexware-Office-Account

---

## Installation

```bash
# 1. Projekt herunterladen
git clone <repo-url> zettle-lexoffice
cd zettle-lexoffice

# 2. Virtuelle Umgebung anlegen
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten installieren
pip install -r requirements.lock

# 4. .env anlegen
cp .env.example .env
chmod 600 .env
```

---

## Konfiguration

### Schritt 1 — Zettle Developer App registrieren

1. **Developer Portal öffnen:**
   [developer.zettle.com](https://developer.zettle.com) → *Create account* oder mit bestehendem Zettle-Account anmelden

2. **Neue App anlegen:**
   - *Apps* → *Create app*
   - Name: z. B. `Lexoffice Import`
   - App type: **API credentials** (kein Redirect-URI nötig)

3. **API-Key erzeugen und kopieren:**
   Den von Zettle bereitgestellten JWT-API-Key (`eyJ...`) sofort sicher speichern.

4. **In `.env` eintragen:**
   ```
   ZETTLE_API_KEY=eyJ...
   ```

> **Hinweis:** Der Account, mit dem die Developer App erstellt wird, muss derselbe sein wie der Zettle-Kassenaccount. Das Script liest ausschließlich Kaufhistorie — keine Schreibrechte auf die Kasse.

---

### Schritt 2 — Lexware-Office-API-Key erstellen

1. Lexware Office öffnen → Profilbild (rechts oben) → **Mein Konto**
2. Linke Navigation: **API & Integrationen** → **API-Keys**
3. *Neuen API-Key erstellen* → Name: `Zettle Import`
4. Den Key sofort kopieren (wird danach nicht mehr angezeigt)
5. In `.env` eintragen:
   ```
   LEXOFFICE_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

### Schritt 3 — Kategorie- und Kontakt-IDs eintragen

Lexware Office ordnet Dienstleistung und Warenverkauf jeweils einer Kategorie
(= Buchungskonto) zu. Außerdem benötigt jeder Sammelbeleg einen Kontakt.

Die UUIDs der Kategorien ermitteln:

```bash
python3 - <<'EOF'
import os, json
from dotenv import load_dotenv
from lexoffice_client import LexofficeClient

load_dotenv()
client = LexofficeClient(os.environ["LEXOFFICE_API_KEY"], {}, "")
cats = client.list_posting_categories()
for c in cats:
    if "19" in c.get("name", "") or "Erlös" in c.get("name", ""):
        print(c["id"], "→", c["name"])
EOF
```

Die passenden Kategorie-UUIDs und die Kontakt-UUID in `.env` eintragen:
```
LEXOFFICE_SERVICE_CATEGORY_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LEXOFFICE_SALES_CATEGORY_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LEXOFFICE_CONTACT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Schritt 4 — Eigene Produkte und Steuersätze konfigurieren (optional)

Ohne weitere Einstellung bleibt die bisherige, erprobte Zuordnung aktiv. Für
eigene Produktnamen oder zusätzliche Steuersätze:

```bash
cp accounting.example.json accounting.json
```

In `accounting.json` lassen sich beliebig viele Produktnamen einer Kategorie
und einem Steuersatz zuordnen. Jede Kategorie verweist auf den Namen einer
Umgebungsvariable, welche die passende Lexware-Kategorie-ID enthält. Danach in
`.env` ergänzen:

```dotenv
ACCOUNTING_CONFIG=accounting.json
```

`accounting.json` ist absichtlich in `.gitignore`. Die Beispieldatei aktiviert
Rabatte mit `"discounts": "useGrossValue"`; dafür wird der von Zettle gemeldete
tatsächliche Bruttowert verwendet. Nicht zugeordnete Produktnamen oder
abweichende Steuersätze führen weiterhin sicher zum Abbruch des betreffenden
Tages.

---

### Fertige `.env`

```
ZETTLE_API_KEY=...
LEXOFFICE_API_KEY=...
LEXOFFICE_SERVICE_CATEGORY_ID=...
LEXOFFICE_SALES_CATEGORY_ID=...
LEXOFFICE_CONTACT_ID=...
# Nur bei eigener Datei erforderlich:
ACCOUNTING_CONFIG=accounting.json
```

---

## Verwendung

```bash
# Virtuelle Umgebung aktivieren (falls noch nicht aktiv)
source .venv/bin/activate

# Gestrigen Tag importieren (Standardverhalten)
python main.py

# Bestimmten Tag importieren
python main.py --date 2025-01-15

# Zeitraum importieren (Bulk)
python main.py --from 2025-01-01 --to 2025-05-31

# Trockenlauf — liest Zettle, schreibt aber nicht in Lexware Office
python main.py --from 2025-01-01 --to 2025-05-31 --dry-run
```

### Beispielausgabe (Bulk-Import)

```
10:15:01 INFO     === Logging to logs/2025-06-01.log ===
10:15:02 INFO     [  1/151] 2025-01-01  ✓  Brutto 312.45 €  (Karte 268.65 €, Bar 43.80 €)  id=abc…
10:15:03 INFO     [  2/151] 2025-01-02  –  kein Umsatz
10:15:04 INFO     [  3/151] 2025-01-03  –  Beleg bereits vorhanden
10:15:05 ERROR    [  4/151] 2025-01-04  ✗  Zettle-Fehler: Request failed after 3 attempts
...
10:18:44 INFO     ──────────────────────────────────────────────────
10:18:44 INFO     Zusammenfassung  (151 Tag(e) verarbeitet)
10:18:44 INFO       ✓ Erfolgreich:    147
10:18:44 INFO       – Übersprungen:     3
10:18:44 INFO       ✗ Fehlgeschlagen:   1
10:18:44 INFO     ──────────────────────────────────────────────────
```

---

## Automatisierung (Cron)

Damit das Script jeden Morgen automatisch den Vortag importiert:

```bash
# Crontab öffnen
crontab -e
```

Folgende Zeile einfügen (läuft täglich um 06:00 Uhr):

```
0 6 * * * /pfad/zu/zettle-lexoffice/.venv/bin/python /pfad/zu/zettle-lexoffice/main.py >> /pfad/zu/zettle-lexoffice/logs/cron.log 2>&1
```

Den korrekten Pfad ermitteln:

```bash
pwd   # zeigt das aktuelle Verzeichnis
```

Für eine monatliche Übertragung am 5. muss der konkrete Zeitraum berechnet
werden; ein bloßer Aufruf ohne Argumente importiert nur den Vortag. Nutze dafür
ein separates Wrapper-Skript oder einen Scheduler, der `--from` und `--to` auf
den vollständigen Vormonat setzt. Führe eine Automatisierung zuerst manuell mit
`--dry-run` aus und stelle sicher, dass nicht mehrere Instanzen parallel laufen.

---

## Logs

Tägliche Logdateien liegen unter `logs/YYYY-MM-DD.log`.
Die Konsolenausgabe zeigt INFO-Level, die Datei enthält zusätzlich
DEBUG-Meldungen. Zugangsschlüssel werden nicht protokolliert. Logs können aber
Geschäftsdaten wie Datum, Summen und Beleg-IDs enthalten und gehören nicht ins
Repository oder in öffentliche Fehlerberichte.

Der Prozess gibt **Exit-Code 1** zurück, wenn mindestens ein Tag fehlgeschlagen ist — geeignet für Monitoring oder Cron-Benachrichtigungen.

---

## Projektstruktur

```
zettle-lexoffice/
├── main.py               # CLI-Einstiegspunkt & Orchestrierung
├── zettle_client.py      # Zettle Purchase API (OAuth2/JWT, Pagination, Retry)
├── lexoffice_client.py   # Lexware API (Voucher-Erstellung, Duplikat-Prüfung)
├── accounting_config.py  # validierte Produkt-, Kategorie- und Steuerregeln
├── accounting.example.json
├── aggregator.py         # Aggregation nach Zahlungsart, Kategorie und Steuer
├── requirements.txt
├── requirements.lock     # reproduzierbar geprüfte Paketversionen
├── SECURITY.md
├── THIRD_PARTY_NOTICES.md
├── .env.example
├── .github/workflows/ci.yml
└── logs/                 # täglich rotierte Logdateien (in .gitignore)
```

---

## Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Tag ohne Umsatz | Übersprungen, kein leerer Beleg |
| Beleg für diesen Tag existiert bereits | Übersprungen (Duplikatschutz) |
| Zettle-API antwortet nicht | 3 Versuche mit exponentiellem Backoff (1s → 2s → 4s) |
| Lexware-GET antwortet vorübergehend nicht | Bis zu 3 Versuche mit Backoff |
| Lexware-POST endet uneindeutig | Kein blindes Wiederholen; nächster Lauf prüft zuerst den Duplikatmarker |
| Unbekannter Zahlungs-/Produkt-Typ | Tag schlägt fehl, kein Beleg wird angelegt |
| Zahlungs- und Produktsumme weichen ab | Tag schlägt fehl, kein Beleg wird angelegt |
| Ungültige Credentials | Sofortiger Abbruch mit Fehlermeldung |
| Fehlende `.env`-Variable | Abbruch vor dem ersten API-Call |

---

## Tests

```bash
python -m unittest discover -v
```

Die Tests führen keine externen API-Aufrufe aus; alle HTTP-Calls sind gemockt.
GitHub Actions führt sie unter Python 3.11 bis 3.13 aus und scannt versionierte
Dateien sowie die Git-Historie auf typische Zugangsdatenmuster.

## Technische Annahmen und Duplikatschutz

- Ein Geschäftstag entspricht Mitternacht bis Mitternacht in
  `Europe/Berlin`, einschließlich Sommer-/Winterzeit.
- Die Summen aus Zahlungs- und Produktpositionen müssen centgenau
  übereinstimmen.
- Die Währung muss EUR sein; Zettle-Steuersatz und konfigurierte Produktregel
  müssen übereinstimmen.
- Belege tragen einen stabilen Marker wie `zettle-import-YYYY-MM-DD-bar`.
  Vor dem Schreiben wird in Lexware Office danach gesucht.
- Der Schutz verhindert normale Wiederholungen, ersetzt aber keine serverseitige
  Idempotenz und schützt nicht sicher vor zwei gleichzeitig laufenden Instanzen.

## Entwicklung und Sicherheit

```bash
python -m unittest discover -v
python scripts/check_secrets.py
```

Bitte lies vor Beiträgen [SECURITY.md](SECURITY.md). Drittanbieter-Lizenzen sind
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) dokumentiert.

## Lizenz

Apache License 2.0. Siehe [LICENSE](LICENSE).
