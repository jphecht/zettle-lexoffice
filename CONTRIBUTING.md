# Mitwirken

Beiträge sind willkommen. Bitte arbeite in einem eigenen Branch und eröffne
einen Pull Request gegen den Entwicklungsbranch.

Vor einem Pull Request:

```bash
python -m unittest discover -v
python scripts/check_secrets.py
```

Neue API-Fälle müssen mit gemockten Tests abgedeckt werden. Tests dürfen keine
echten Zettle- oder Lexware-Office-Zugänge verwenden. Änderungen an Steuern,
Rundung, Zahlungsarten, Produktzuordnung oder Duplikatschutz benötigen außerdem
Tests für Fehlerfälle und Cent-Rundungen.

Bitte committe niemals `.env`, Logs, echte API-Antworten oder sonstige
personenbezogene beziehungsweise buchhalterische Daten. Sicherheitsprobleme
werden gemäß [SECURITY.md](SECURITY.md) privat gemeldet.
