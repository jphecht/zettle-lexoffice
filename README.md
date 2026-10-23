# Zettle → Lexware Office

Überträgt Tageseinnahmen aus Zettle (PayPal POS) als Sammelbelege nach
Lexware Office. Das Projekt ist unabhängig und nicht mit PayPal oder Lexware
verbunden.

**Was das Script macht:**
- Ruft alle Kassenvorgänge eines Tages über die Zettle Purchase API ab
- Trennt Kartenzahlungen und Barzahlungen
- Rechnet konfigurierte Umsatzsteuersätze aus Bruttobeträgen heraus
- Kann explizit von Zettle gemeldetes Trinkgeld nach Kassenbenutzer und Rolle aufteilen
- Legt Bar- und Kartenumsätze als Sammelbelege (`salesinvoice`) an
- Legt Rückerstattungen getrennt als Verkaufsgutschriften (`salescreditnote`) an
- Schützt vor Doppel-Import und überspringt umsatzlose Tage
- Bricht bei unbekannten oder nicht abgestimmten Daten ab, statt sie zu schätzen

> **Wichtige Einschränkung:** Ohne eigene Konfiguration bleibt das bisherige
> Verhalten mit `Dienstleistung`, `Beispiel Zusatzleistung`, `Verkauf` und 19 % erhalten.
> Eine eigene JSON-Datei kann Namen, Kategorien, Steuersätze, Rabatte,
> Trinkgeldregeln und Rückerstattungen konfigurieren. Gutscheine, Gebühren und
> sonstige Stornos bleiben zunächst gesperrt. Erkennt das Programm eine nicht konfigurierte
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

#### Trinkgeld nach Kassenbenutzer

Zettle liefert pro Kauf eine stabile `userId` und je Zahlung den expliziten
`gratuityAmount`. Im Modus `byCashier` wird ausschließlich dieser gemeldete
Trinkgeldbetrag verwendet; eine bloße Differenz zwischen Produkt- und
Zahlungssumme wird niemals als Trinkgeld geraten.

Jeder Kassenbenutzer wird in `adjustments.tips.cashiers` einer Rolle zugeordnet:

- `entrepreneur`: Trinkgeld wird mit der konfigurierten Erlöskategorie und dem
  ausdrücklich festgelegten Umsatzsteuersatz gebucht.
- `employee`: Trinkgeld wird mit 0 % Umsatzsteuer und einer eigenen Kategorie
  gebucht. Mehrere Mitarbeiter können getrennte Kategorien verwenden oder eine
  gemeinsame Kategorie teilen.

Die Beispieldatei enthält nur fiktive Benutzer-IDs. Sie müssen durch die
`userId` aus den eigenen Zettle-Käufen ersetzt werden. Namen dienen lediglich
der Lesbarkeit; die Zuordnung erfolgt ausschließlich über die ID. Bei fehlender
oder unbekannter ID bricht der Tag vor dem Schreiben nach Lexware ab.

> **Buchhaltungshinweis:** Der Lexware-Voucher-Endpunkt akzeptiert in einem
> Verkaufsbeleg nur unterstützte Kategorien vom Typ `income`. Für
> Mitarbeiter-Trinkgeld muss deshalb eine in der eigenen Lexware-Organisation
> verfügbare Kategorie gewählt werden, die fachlich zur Abbildung des
> durchlaufenden Postens passt. Ist keine solche Kategorie verfügbar oder die
> Behandlung ungeklärt, `"tips": "reject"` beibehalten. Die Steuerfreiheit von
> Arbeitnehmer-Trinkgeld setzt insbesondere die Voraussetzungen des
> [§ 3 Nr. 51 EStG](https://www.gesetze-im-internet.de/estg/__3.html) voraus.

#### Rückerstattungen als getrennte Gutschriften

Mit `"refunds": "separateCreditNote"` werden Zettle-Rückerstattungen nicht mit
den Tagesumsätzen verrechnet. Das Programm erzeugt stattdessen je Zahlungsart
einen eigenen Lexware-Beleg vom Typ `salescreditnote`. Dadurch bleiben
ursprünglicher Umsatz und Erstattung getrennt nachvollziehbar.

Verarbeitet werden ausschließlich eindeutig gekennzeichnete Zettle-Vorgänge mit
`refund: true`, negativen Produkt- und Zahlungsbeträgen sowie Referenzen auf den
Ursprungskauf und die ursprüngliche Zahlung. Teilrückerstattungen werden mit dem
tatsächlich erstatteten Betrag erfasst. Fehlende Referenzen, widersprüchliche
Vorzeichen oder Erstattungen von Trinkgeld führen vor jedem Lexware-Schreibzugriff
zum Abbruch des gesamten Tages.

Ohne eigene Konfiguration bleibt `refunds` auf `reject`; das bisherige Verhalten
ändert sich daher nicht. Die verwendeten Produktnamen und Steuersätze müssen auch
bei der Rückerstattung eindeutig in `accounting.json` zugeordnet sein.

#### Gutschein-Vorprüfung nach Branche und Sortiment

Gutscheine werden noch nicht nach Lexware übertragen. Sie können jedoch bereits
für die spätere Einrichtung fachlich vorgeprüft werden:

```json
"giftCards": {
  "mode": "reviewRequired",
  "industryProfile": "hairAndBeauty",
  "redeemableCategories": ["service", "sales"]
}
```

Das Branchenprofil ist nur eine beschreibende Vorauswahl. Maßgeblich sind die
Kategorien, für die der Gutschein nach seinen Bedingungen tatsächlich
eingelöst werden darf, und deren konfigurierte Steuersätze:

- genau ein Steuersatz: `singlePurposeCandidate`
- mehrere Steuersätze: `multiPurposeCandidate`

Beide Ergebnisse sind ausdrücklich nur Prüfvorschläge. Auch bei nur einem
Steuersatz müssen insbesondere Leistungsort und Gutscheinbedingungen bestätigt
werden. Das Programm aktiviert daher keine Buchungslogik automatisch.
Zettle-Produkte vom Typ `GIFTCARD` und Gutscheinzahlungen werden weiterhin mit
einer verständlichen Fehlermeldung vor jedem Lexware-Schreibzugriff blockiert.
Ein später neu hinzukommender Steuersatz verändert die Vorprüfung anhand der
konfigurierten einlösbaren Kategorien.

Ohne Vorprüfung bleibt `"giftCards": "reject"`. Ein Modus zur tatsächlichen
Verbuchung von Einzweck- oder Mehrzweckgutscheinen wird absichtlich noch nicht
akzeptiert.

Die Vorprüfung lässt sich ohne Zugangsdatenzugriff und ohne API-Aufruf anzeigen:

```bash
python main.py --review-gift-cards
```

Der Befehl liest ausschließlich die lokale `accounting.json` und meldet Branche,
einlösbare Kategorien, gefundene Steuersätze sowie den Prüfvorschlag.

#### Zettle-Produktkatalog prüfen

Der Produktkatalog kann mit einem ausschließlich lesenden Zettle-Zugriff gegen
die Buchungskonfiguration geprüft werden:

```bash
python main.py --review-catalog
```

Der Befehl verwendet nur den Zettle-Berechtigungsumfang `READ:PRODUCT` und ruft
Lexware Office nicht auf. Er verändert weder den Zettle-Katalog noch lokale
Konfigurationsdateien. Gemeldet werden:

- alle im Katalog gefundenen Steuersätze,
- nicht zugeordnete Produkte,
- Abweichungen zwischen Zettle und `accounting.json`,
- Produkte mit mehreren oder ungültigen Steuersätzen,
- 0-%-Produkte, deren konkreter Steuergrund noch geprüft werden muss,
- Gutscheinprodukte, die weiterhin für den Import gesperrt sind.

Für die Gutschein-Vorprüfung werden die tatsächlich gefundenen Steuersätze der
als einlösbar konfigurierten Kategorien verwendet. Kommt dort beispielsweise
erstmals ein 7-%-Produkt zu bisherigen 19-%-Produkten hinzu, wechselt der
Prüfvorschlag auf `multiPurposeCandidate`; die Abweichung bleibt gleichzeitig
ein Blocker.

Unbekannte Produkte, Steuerabweichungen, Gutscheinprodukte und ungeklärte
0-%-Produkte gelten als Blocker. Dann endet der Befehl mit Exit-Code 1. Dadurch
kann die Prüfung später vor einen Monatsimport oder eine Automatisierung
geschaltet werden. Der Katalog ersetzt keine Prüfung der tatsächlichen
Gutscheinbedingungen; die Branche bleibt nur eine Vorauswahl.

#### Konfigurationsvorschlag erzeugen

Aus dem gelesenen Katalog kann ein JSON-Entwurf erzeugt werden:

```bash
python main.py --propose-config
```

Die Ausgabe erfolgt ausschließlich auf der Konsole. Bestehende Regeln bleiben
erhalten; für unbekannte Produkte werden getrennte Platzhalter-Kategorien
angelegt. Gutscheinprodukte, widersprüchliche Steuersätze und ungeklärte
0-%-Produkte werden im `_review`-Abschnitt aufgeführt.

Jeder Entwurf enthält `"proposalStatus": "reviewRequired"` und wird vom
normalen Import absichtlich abgelehnt. Erst nachdem Produktzuordnungen,
Lexware-Kategorien, 0-%-Steuergründe und Gutscheinbedingungen geprüft wurden,
darf der Status manuell auf `confirmed` gesetzt und die Datei als
`accounting.json` übernommen werden. Das Programm überschreibt niemals eine
bestehende Konfiguration.

#### Monats-/Zeitraumprüfung vor der Übertragung

Vor einem Monatsimport kann der gesamte Zeitraum ausschließlich lesend aus
Zettle geprüft werden:

```bash
python main.py --from 2026-09-01 --to 2026-09-30 --preflight
```

Dieser Befehl greift nicht auf Lexware Office zu und legt keine Belege an. Er
zeigt leere, vollständig geprüfte und fehlerhafte Tage sowie Einnahmen,
Erstattungen und Steuerkorrekturen getrennt nach Steuersatz und
Buchungskategorie. Trinkgelder werden zusätzlich nach Unternehmer- und
Mitarbeiterrolle ausgewiesen.

Nicht konfigurierte Produkte, ungeklärte 0-%-Artikel, Steuersätze außerhalb
0/7/19, Gutscheinvorgänge und fehlerhafte Tage blockieren die Freigabe. Ein
fehlerhafter Tag wird vollständig aus den Summen ausgeschlossen; der Bericht
bezeichnet die Summen deshalb ausdrücklich als Summen der vollständig geprüften
Tage. Bei einem Blocker endet die Prüfung mit Exit-Code 1. Eine technisch
erfolgreiche Prüfung ersetzt nicht die fachliche Kontrolle der
Buchungskonfiguration.

---

### Fertige `.env`

```
ZETTLE_API_KEY=...
LEXOFFICE_API_KEY=...
LEXOFFICE_SERVICE_CATEGORY_ID=...
LEXOFFICE_SALES_CATEGORY_ID=...
# Nur bei aktivierter Trinkgeldzuordnung:
LEXOFFICE_OWNER_TIPS_CATEGORY_ID=...
LEXOFFICE_EMPLOYEE_TIPS_CATEGORY_ID=...
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

# Monatsprüfung — liest nur Zettle und greift nicht auf Lexware Office zu
python main.py --from 2026-09-01 --to 2026-09-30 --preflight

# Gutschein-Konfiguration lokal prüfen — keine API-Aufrufe
python main.py --review-gift-cards

# Zettle-Produktkatalog nur lesend prüfen — kein Lexware-Aufruf
python main.py --review-catalog

# Unbestätigten Konfigurationsentwurf auf der Konsole ausgeben
python main.py --propose-config
```

### Beispielausgabe (Bulk-Import)

```
10:15:01 INFO     === Logging to logs/2025-06-01.log ===
10:15:02 INFO     [  1/151] 2025-01-01  ✓  Umsatz 312.45 €  Erstattungen 12.00 €  Netto 300.45 €  ids=['abc…', 'def…']
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
├── catalog_review.py     # lesende Produkt- und Steuersatzprüfung
├── config_proposal.py    # sicherer, unbestätigter Konfigurationsentwurf
├── preflight_report.py   # lesender Monatsbericht vor der Übertragung
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
