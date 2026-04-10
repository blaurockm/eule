# Eule — Produktvision

Eule ist das zentrale Beobachtungs- und Analysewerkzeug im Trading-Ökosystem.
Eule handelt nicht, Eule schaut hin.

## Mission

Alle relevanten Portfolio-Informationen aus verteilten Brokern, Datenbanken und
Strategien in einem deterministischen CLI zusammenführen — maschinenlesbar (`--format json`)
und menschenlesbar (`--format markdown`).

## Kernaufgaben

### 1. Bestand — *Was habe ich?*

Positionen aller Broker aggregieren (IBKR, TR, Tradier, Willbe, IG),
nach Typ aufschlüsseln (Aktien, Optionen, Anleihen, Gold),
Allokation gegen Soll prüfen, Gesamtbild liefern.

### 2. Bewertung — *Wie läuft es?*

Trades auswerten (Roundtrips, PnL, Haltedauer), Strategie-Performance
messen (Sharpe, Drawdown, Win-Rate), Kaufthesen gegen Live-Daten prüfen.

### 3. Pipeline — *Was kommt als nächstes?*

Kandidaten finden, bewerten, auf Watchlist setzen, Fills tracken,
Positionen schließen, Morning Briefs generieren. Aktuell EP-Strategie,
die Struktur ist strategieunabhängig.

### 4. Betrieb — *Läuft alles?*

Health-Checks der Hase-Runtime, Broker-Konnektivität testen,
Konfiguration validieren.

## Prinzipien

- **Deterministisch** — kein LLM in Eule, nur Datenverarbeitung
- **Dual-Output** — jeder Befehl liefert Markdown (Mensch) und JSON (Maschine)
- **Kein Broker-Zugriff für Execution** — Eule liest, handelt nicht
- **Ökosystem-Baustein** — konsumiert Hase (DB/API), Hamster (Data Lake), liefert an Wachtel (Telegram), Fuchs (Monitoring)
