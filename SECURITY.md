# Sicherheitsrichtlinie

## Sicherheitslücken melden

Bitte veröffentliche mögliche Sicherheitslücken, Zugangsdaten oder
personenbezogene Daten nicht in einem öffentlichen Issue.

Nutze stattdessen auf GitHub unter **Security → Advisories → New draft security
advisory** den privaten Meldeweg. Falls dieser im Repository noch nicht
aktiviert ist, kontaktiere den Maintainer über sein GitHub-Profil, ohne das
Geheimnis selbst mitzuschicken.

Wenn ein echter Schlüssel versehentlich offengelegt wurde, widerrufe ihn sofort
bei Zettle beziehungsweise Lexware Office. Das Entfernen aus einem späteren
Commit reicht nicht aus, weil der Wert in der Git-Historie verbleiben kann.

## Umgang mit Zugangsdaten

- `.env` niemals committen oder weitergeben.
- Lokal nur für den eigenen Benutzer lesbar machen: `chmod 600 .env`.
- Logs vor dem Teilen auf Kunden-, Beleg- und Kontodaten prüfen.
- Für Tests ausschließlich Testwerte und gemockte HTTP-Aufrufe verwenden.

Unterstützt wird jeweils der aktuelle Stand des Branches `main`.
