# Zettle → Lexoffice

Überträgt die Tageseinnahmen aus dem Zettle POS automatisch als Sammelbeleg nach Lexoffice.

**Was das Script macht:**
- Ruft alle Kassenvorgänge eines Tages über die Zettle Purchase API ab
- Trennt Kartenzahlungen und Barzahlungen
- Rechnet 19 % MwSt heraus (Brutto → Netto + Steuer)
- Legt einen Tagessammelbeleg (`salesinvoice`) in Lexoffice an
- Schützt vor Doppel-Import und überspringt umsatzlose Tage

---

## Voraussetzungen

- Python 3.11 oder neuer
- Ein aktiver Zettle-Account (PayPal POS)
- Ein aktiver Lexoffice-Account

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
pip install -r requirements.txt

# 4. .env anlegen
cp .env.example .env
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

3. **Zugangsdaten kopieren:**
   Nach dem Anlegen erscheinen **Client ID** und **Client Secret** einmalig — sofort sichern.

4. **In `.env` eintragen:**
   ```
   ZETTLE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ZETTLE_CLIENT_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

> **Hinweis:** Der Account, mit dem die Developer App erstellt wird, muss derselbe sein wie der Zettle-Kassenaccount. Das Script liest ausschließlich Kaufhistorie — keine Schreibrechte auf die Kasse.

---

### Schritt 2 — Lexoffice API-Key erstellen

1. Lexoffice öffnen → Profilbild (rechts oben) → **Mein Konto**
2. Linke Navigation: **API & Integrationen** → **API-Keys**
3. *Neuen API-Key erstellen* → Name: `Zettle Import`
4. Den Key sofort kopieren (wird danach nicht mehr angezeigt)
5. In `.env` eintragen:
   ```
   LEXOFFICE_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

### Schritt 3 — Erlöskategorie-ID herausfinden

Lexoffice ordnet jeden Buchungsposten einer Kategorie (= Buchungskonto) zu.
Für 19 % Umsatzsteuer ist das typischerweise **„Erlöse 19 % USt"**.

Die UUID dieser Kategorie ermitteln:

```bash
python3 - <<'EOF'
import os, json
from dotenv import load_dotenv
from lexoffice_client import LexofficeClient

load_dotenv()
client = LexofficeClient(os.environ["LEXOFFICE_API_KEY"], "")
cats = client.list_posting_categories()
for c in cats:
    if "19" in c.get("name", "") or "Erlös" in c.get("name", ""):
        print(c["id"], "→", c["name"])
EOF
```

Die ausgegebene UUID in `.env` eintragen:
```
LEXOFFICE_REVENUE_CATEGORY_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

### Fertige `.env`

```
ZETTLE_CLIENT_ID=...
ZETTLE_CLIENT_SECRET=...
LEXOFFICE_API_KEY=...
LEXOFFICE_REVENUE_CATEGORY_ID=...
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

# Trockenlauf — kein Schreibzugriff auf Lexoffice
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

## Tägliche Automatisierung (Cron)

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

---

## Logs

Tägliche Logdateien liegen unter `logs/YYYY-MM-DD.log`.
Die Konsolenausgabe zeigt INFO-Level, die Datei enthält zusätzlich DEBUG-Meldungen (HTTP-Details, Paginierung).

Der Prozess gibt **Exit-Code 1** zurück, wenn mindestens ein Tag fehlgeschlagen ist — geeignet für Monitoring oder Cron-Benachrichtigungen.

---

## Projektstruktur

```
zettle-lexoffice/
├── main.py               # CLI-Einstiegspunkt & Orchestrierung
├── zettle_client.py      # Zettle Purchase API (OAuth2/JWT, Pagination, Retry)
├── lexoffice_client.py   # Lexoffice API (Voucher-Erstellung, Duplikat-Prüfung)
├── aggregator.py         # MwSt-Herausrechnung, Karte/Bar-Trennung
├── requirements.txt
├── .env.example
└── logs/                 # täglich rotierte Logdateien (in .gitignore)
```

---

## Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Tag ohne Umsatz | Übersprungen, kein leerer Beleg |
| Beleg für diesen Tag existiert bereits | Übersprungen (Duplikatschutz) |
| Zettle-API antwortet nicht | 3 Versuche mit exponentiellem Backoff (1s → 2s → 4s) |
| Lexoffice-API antwortet nicht | wie oben |
| Ungültige Credentials | Sofortiger Abbruch mit Fehlermeldung |
| Fehlende `.env`-Variable | Abbruch vor dem ersten API-Call |

---

## Tests

```bash
python -m unittest discover -v
```

65 Tests, keine externen Abhängigkeiten zur Laufzeit (alle HTTP-Calls gemockt).
