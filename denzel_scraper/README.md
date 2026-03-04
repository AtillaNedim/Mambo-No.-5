# Denzel Scraper (Python)

Scraped alle Fahrzeuge von `https://www.denzel.at/gebrauchtwagen/suche` ueber alle Seiten (`page=0..n`) und speichert die Daten als JSON.

Enthaelt pro Fahrzeug u. a.:
- Titel, Marke, Modell
- Preis, Erstzulassung, Kilometer, Technik-Infos
- Detail-Link
- Bild-Link von der Suchseite
- Detailseiten-Daten (Beschreibung, technische Daten, Ansprechpartner, Standort)
- Bild-Links aus der Fahrzeug-Galerie
- Aehnliche Fahrzeuge inkl. Bild-Link

## Setup

```bash
cd denzel_scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Voller Crawl (alle Seiten + Detailseiten)

```bash
python scraper.py --verbose
```

Output (Standard):
`denzel_scraper/output/denzel_vehicles.json`

## Nützliche Optionen

```bash
# Nur erste 3 Suchseiten testen
python scraper.py --max-pages 3 --verbose

# Ohne Detailseiten (schneller)
python scraper.py --no-details --verbose

# Mit bestehendem Filter-Link scrapen
python scraper.py --base-url "https://www.denzel.at/gebrauchtwagen/suche?bauart=0&topangebot=nein&sonderaktion=nein"
```

## Hinweis

Die Website kann Struktur aendern. Falls sich CSS-Klassen/HTML aendern, muessen Selektoren in `scraper.py` angepasst werden.
