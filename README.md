# Ediktsdatei – Versteigerungen Steiermark (Raum Graz/Weiz/Kumberg)

Kleine Web-App, die gerichtliche Versteigerungen (Zwangsversteigerungen von
Liegenschaften) aus der österreichischen [Ediktsdatei](https://edikte.justiz.gv.at)
für die **Steiermark** abruft und den Raum **Graz / Weiz / Kumberg** hervorhebt.

## Wie es funktioniert

GitHub Pages kann kein Python ausführen und ein reiner Browser darf die
Ediktsdatei wegen CORS nicht direkt abfragen. Deshalb:

1. **GitHub Actions** führt `edikte_search.py` nach Zeitplan aus (täglich)
   und erzeugt `public/index.html`.
2. Die Action veröffentlicht diese Seite auf **GitHub Pages**.
3. Die Seite bietet ein Live-Suchfeld und einen Umschalter zwischen
   „Raum Graz/Weiz/Kumberg" und „alle Steiermark".

## Manuell aktualisieren

Actions-Tab → Workflow „Ediktsdatei-Report bauen & veröffentlichen" →
**Run workflow**.

## Lokal ausführen

```bash
pip install -r requirements.txt
python edikte_search.py            # erzeugt versteigerungen_relevant.html und öffnet sie
python edikte_search.py --debug    # zeigt die Formularfelder
```

## Suchgebiet anpassen

In `edikte_search.py` die Listen `RELEVANT_KEYWORDS`, `RELEVANT_PLZ_PREFIXES`
und `RELEVANT_PLZ_EXACT` bearbeiten.

Angaben ohne Gewähr. Datenquelle: edikte.justiz.gv.at
