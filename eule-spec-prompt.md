# Eule 🦉 — Spezifikations-Prompt für Claude Code

> Diesen Prompt an Claude Code geben um eine technische Spezifikation schreiben zu lassen.
> Die Architektur klärst du dann selbst mit Claude Code.

---

## Prompt

Ich brauche eine technische Spezifikation für **Eule** — ein eigenständiges Python-Projekt (CLI-Tool) das als Portfolio Monitor, Trade Journal und News-Briefing-System über mehrere Broker hinweg dient.

### Kontext

Eule ist Teil eines Trading-Ökosystems mit Tiernamen:
- **Hase** — Live Trading System (Python, läuft auf Server "systematic")
- **Hamster** — Financial Data Lake (Delta Lake, IBKR/Crypto/FRED Daten)
- **Dachs** — Vectorized Backtesting Research
- **Igel** — Event-Driven Backtesting
- **Fuchs** — Supervisor + Vue Dashboard (Vercel/Supabase)
- **Elster** — Performance-Analyse CLI innerhalb von Hase (Tagesbasis, nur Hase-Strategien)

Eule ergänzt Elster: während Elster nur Hase-Strategien auf Tagesbasis analysiert, deckt Eule **alle Broker** auf **Trade-Ebene** ab.

### Phasen-Plan

**Phase 1 (MVP) — Positions-Übersicht + Monitor** ⭐ Priorität
Am 26.03.2026 haben wir das gesamte Portfolio manuell dokumentiert (12 Positionen über 4 Konten, ~1h Arbeit). Das muss automatisch gehen. Phase 1 fokussiert sich auf Positions-Aggregation und Monitoring, NICHT auf Trade-Journal.

**Phase 2 — Trade Journal + Analyse**
Kommt danach, wenn Phase 1 steht.

---

## Architektur-Entscheidung: KI-Nutzung

### Eule = Daten-Tool, Wachtel = KI-Hirn

Eule ist ein **deterministisches CLI-Tool** das Daten sammelt, aggregiert und strukturiert ausgibt. Eule selbst nutzt **keine KI / kein LLM**. Die Interpretation, Analyse und Empfehlung macht **Wachtel** (der KI-Agent), der Eule als Tool aufruft.

```
┌─────────────┐     CLI-Aufruf      ┌──────────────┐
│  Wachtel 🪶  │ ──────────────────→ │   Eule 🦉    │
│  (KI-Agent)  │                     │  (Python CLI) │
│              │ ←────────────────── │              │
│  Interpretiert│    JSON / Markdown  │  Sammelt,     │
│  Urteilt      │    strukturierte    │  aggregiert,  │
│  Empfiehlt    │    Daten           │  berechnet    │
└─────────────┘                     └──────────────┘
```

### Was Eule macht (deterministisch, kein LLM):
- Positionen von APIs abrufen und aggregieren
- P&L berechnen (inkl. FX-Konvertierung)
- 50%-Regel für Optionen checken (reine Mathe: sold_premium × 0.5 vs current_value)
- Verfall-Warnungen berechnen (DTE < 7 → Warnung)
- Allokation berechnen (Ist vs. Soll, reine Prozentrechnung)
- Bond-Kupon und Fälligkeitsdaten tracken
- Goldpreis abrufen und physisches Gold bewerten
- News-Headlines für Ticker sammeln (Web Search, kein Urteil)
- Earnings-Countdown abrufen (SharpeTwo)
- IV Rank / VRP abrufen (SharpeTwo)
- **Alles als strukturierten Output liefern (JSON + Markdown)**

### Was Wachtel macht (KI-gestützt, nutzt Eule-Output):
- `eule positions --json` aufrufen → Portfolio-Übersicht interpretieren
- Thesis-Check: Freitext Exit-Kriterien aus `positions-bh.md` gegen Eule-Daten prüfen ("Revenue-Wachstum < 50%? Lass mich die Earnings-Daten anschauen...")
- News bewerten: "Ist diese Headline relevant für die Position?"
- Briefing formulieren: Aus Eule-Rohdaten eine natürliche, priorisierte Zusammenfassung bauen
- Empfehlungen geben: "CDE Put ist bei 48% Gewinn — fast bei 50%-Regel, ich würde bald schließen"
- Kontext einbeziehen: Marktlage, Playbook-Regeln, Erfahrungen aus MEMORY.md

### Warum diese Trennung?
1. **Eule bleibt einfach, testbar, deterministisch** — keine LLM-Halluzinationen in den Daten
2. **Keine API-Kosten** pro Eule-Aufruf — LLM-Kosten fallen nur bei Wachtel an (eh schon laufend)
3. **Wachtel kann mehr Kontext** — er kennt Playbooks, Preferences, Marktlage, Geschichte
4. **Thesis-Check gegen Freitext** ist genau das was LLMs gut können — aber das braucht keinen zweiten LLM-Aufruf in Eule, Wachtel macht das on-the-fly
5. **Eule-Output ist auch ohne KI nützlich** — `eule positions` im Terminal ist sofort lesbar

### Output-Formate

Jeder Eule-Befehl unterstützt zwei Output-Modi:
- `--format markdown` (Default): Menschenlesbarer Markdown-Output für Terminal/Chat
- `--format json`: Maschinenlesbarer JSON-Output für Wachtel-Integration

Wachtel nutzt `--format json` für programmatische Verarbeitung, z.B.:
```bash
eule positions --format json | # Wachtel liest das, vergleicht mit Thesen, schreibt Briefing
eule options --format json     | # Wachtel checkt 50%-Regel im Kontext des Playbooks
```

---

## Phase 1: Positions + Monitor (MVP)

### Säule 1: Positions (📊) — Kern des MVP

**Live-Positions-Übersicht über alle Konten:**
- Aggregierte Übersicht: Ticker, Konto, Asset-Typ, Size, Entry, Aktueller Kurs, Unrealized P&L
- Exposure-Übersicht: nach Asset-Klasse, nach Konto, nach Währung

**Optionen-Tracking (CSP/CC):**
- Offene Short Puts und Covered Calls tracken
- Pro Option: Underlying, Strike, Verfall, Prämie kassiert, aktueller Wert
- **50%-Regel-Alert:** Automatisch flaggen wenn eine Option 50% des Verkaufspreises erreicht hat → "Schließen und neuen Zyklus starten?"
- Verfall-Warnungen (7 Tage, 3 Tage, 1 Tag)
- Roll-Entscheidung vorbereiten (aktueller Wert, Delta, Abstand zum Strike)

**Bond-Tracking:**
- Anleihen mit Nennwert, Kupon, Fälligkeitsdatum, Rating
- Kupon-Zahlungs-Tracking (nächste Zahlung, Jahresertrag)
- Fälligkeits-Erinnerungen
- Credit-Rating-Änderungen flaggen

**Physische Assets:**
- Manueller Config-Eintrag für physisches Gold (Menge, Kaufpreis, Lagerort, Gebühren)
- Aktueller Wert via Goldpreis-Abfrage

**Portfolio-Allokation:**
- Soll/Ist-Vergleich gegen definierte Zielallokation:
  - Kern (Aktien + ETFs): 60-70%
  - Opportunistisch (EP-Trades, Einzelaktien): 20-30%
  - Gold: 5-15%
  - Anleihen: 10-25%
- Warnung wenn eine Kategorie deutlich vom Ziel abweicht
- Warnung wenn eine Einzelposition >15% des Portfolios ist

**Thesis-Tracker:**
- Jede Position hat eine dokumentierte Thesis + Exit-Kriterien
- Referenz: `trading-collab/positions-bh.md` (manuell gepflegt)
- Eule kann Exit-Kriterien automatisch prüfen wo möglich:
  - "Kurs unter $X" → prüfbar via Live-Kurs
  - "Revenue-Wachstum unter X%" → prüfbar nach Earnings via Web
  - "Rating Downgrade" → prüfbar für Anleihen
- Optional: `positions-bh.md` automatisch generieren/aktualisieren

### Säule 2: Monitor / Briefing (📰) — Teil des MVP

**News-Scan** für alle offenen Positionen (Web Search)
**Earnings-Countdown** via SharpeTwo API (`tickers-info/` → `days_until_next_earnings`)
- ⚠️ Alert wenn < 14 Tage (B&H: Entscheidung vor Earnings treffen)
**Vol-Alerts** via SharpeTwo: IV-Spikes, VRP-Änderungen, Regime-Wechsel
**Analyst-Änderungen** via Finviz (Upgrades/Downgrades)
**Bond-Emittenten-News** für Air Baltic und ähnliche High-Yield Positionen

**Zusammenfassendes Briefing als Markdown-Output:**

```
🦉 Eule — Portfolio Update (26.03.2026)

📊 Gesamt: ~€31,500 über 4 Konten
   IBKR: $14,100 | TR: €5,135 | Tradier: $1,313 | Willbe: €13,375

📈 Gewinner: CDE ex-NGD +36%, Gold +7.3%
📉 Verlierer: Green Thumb -50%, TMC -32%, CRWV -30%

⚠️  Aktionen:
   - IBKR CC $76 verfällt am 02.04 → neuen Call planen
   - CDE Put bei 50% Gewinn? Aktuell -20%, halten.
   - TMC Earnings MORGEN — aufpassen!
   - PBB Anleihe fällig Aug 2026 → Re-Invest planen

📰  News:
   - CRWV: BofA Upgrade auf Buy, Target $100
   - CDE: New Gold Übernahme abgeschlossen
   - Air Baltic: Verlust reduziert auf -€44M, Lettland-Wahlen beeinflussen Funding
   
🔇  Keine News: IBKR, S&P500 ETF, STOXX600 ETF, Gold ETC
```

---

## Phase 2: Trade Journal + Analyse

### Säule 3: Journal (📒)

- Multi-Broker Trade-Import mit einheitlichem Trade-Format
- Analyse auf Trade-Ebene (einzelne Entries/Exits, nicht Tagesaggregation)
- Setup-Tagging: EP-Trade, RS-Breakout, Earnings Play, Hase-Systematisch, SharpeTwo-Signal, Covered Call, Cash-Secured Put, B&H, Dip-Buy etc.
- Fehler-Tagging: zu früh raus, kein Stop, FOMO-Entry, Oversize, Ondo-Moment etc.
- R-Multiple Tracking: initiales Risiko (Stop-Distance) erfassen → R berechnen
- Cross-Broker P&L mit FX-Konvertierung (USD, EUR, GBP, Crypto → Basiswährung EUR)
- Reports: Weekly/Monthly Performance, Equity Curve, Setup-Vergleich, Fehler-Analyse
- Konsistenz-Check: Abgleich Journal ↔ Broker ("MSFT bei IBKR nicht im Journal!")

---

## Broker-Landschaft & aktuelle Positionen

### Konten-Übersicht

| Konto | Broker | Asset-Klassen | API | Positions-API |
|-------|--------|--------------|-----|---------------|
| IBKR | Interactive Brokers | Aktien, Optionen, Futures | Flex Queries / REST | Ja — via Hase API |
| TR | Trade Republic | Aktien, ETFs, Anleihen | ❌ Keine API | Manuell (CSV/Config) |
| Tradier | Tradier | Aktien, Optionen (US) | REST API | REST |
| Willbe | Willbe (Gold) | Physisches Gold | ❌ Keine API | Manuell (Config) |

Zusätzliche Broker (für Phase 2 / Hase-Trades):
| Konto | Broker | Asset-Klassen | API |
|-------|--------|--------------|-----|
| IG | IG | CFDs | REST API |
| Kraken | Kraken | Crypto Spot + Futures | REST API |
| Bitunix | Bitunix | Crypto Perps | REST API |
| Hyperliquid | Hyperliquid | Crypto Perps DEX | REST/WS API |

### Referenz-Dokumente (LIES DIESE SELBST!)

| Was | Pfad |
|-----|------|
| Aktuelle Positionen + Thesen + Exit-Kriterien | `~/fin/trading-collab/positions-bh.md` |
| B&H Playbook (inkl. CSP-Einstieg, 50%-Regel) | `~/fin/trading-collab/buy-and-hold-playbook.md` |
| EP Daily Playbook | `~/fin/trading-collab/ep-daily-playbook.md` |
| Hase DB-Zugriff + Trade-Queries | `~/fin/hase/hase/elster/data.py` |
| Hase Environment-Configs (.env, config.json) | `~/fin/hase/run/real/ibkr-one/`, `~/fin/hase/run/real/ibkr-two/` |
| Hase REST API (Positionen, Balance, Orders) | `~/fin/hase/hase/api/` — Endpoints: `/debug/positions`, `/debug/balance`, `/debug/orders/pending` |
| Elster Metriken (wiederverwendbar) | `~/fin/hase/hase/elster/metrics.py` |
| ShadowPortfolio (mark_to_market, expire_options) | `~/fin/hase/hase/popodienst/shadowportfolio.py` |
| Hamster CLI (Live Quotes) | `~/fin/hamster/` — `hamster market quote`, `hamster market history` |
| Eule Backlog + offene Fragen | `~/fin/trading-collab/skills-backlog.md` (Abschnitt 5) |

### Externe APIs (Kurzreferenz)

- **Hase API:** `localhost:8767` (real-ibkr), `:8768` (real2-ibkr), `:8776` (staging) — REST, kein Auth
- **SharpeTwo:** `https://api.sharpetwo.com/api/` — Auth: `SharpeTwo-API-KEY` Header — Vol Analytics, Earnings Countdown, VRP
- **Finviz:** `https://finviz.com/quote.ashx?t={TICKER}` — Web Scraping, nur US-Aktien
- **Hamster CLI:** `hamster market quote/history/chain/search/scan` — auf systematic via Poetry
- **Goldpreis:** Web-Abfrage (z.B. bullion-rates.com) für physisches Gold-Bewertung

---

## CLI-Befehle (Entwurf)

### Phase 1 (MVP)

```bash
eule positions                               # Alle offenen Positionen, cross-broker
eule positions --broker ibkr                 # Nur IBKR
eule positions --type options                # Nur Optionen (CSP/CC)
eule positions --type bonds                  # Nur Anleihen
eule allocation                              # Soll/Ist Allokation
eule briefing                                # Briefing (News + Earnings + Vol-Alerts + Optionen-Status)
eule options                                 # Optionen-Dashboard (50%-Regel, Verfall, Rolls)
eule thesis                                  # Thesis-Check: Exit-Kriterien prüfen für alle Positionen
eule thesis TICKER                           # Thesis-Check für einzelnen Ticker
```

### Phase 2 (Journal)

```bash
eule import --broker ibkr                    # Trades importieren
eule import --broker tradier
eule import --file trades.csv                # CSV-Import (Trade Republic etc.)
eule add                                     # Trade manuell hinzufügen (interaktiv)
eule tag <trade-id> --setup ep --error none  # Trade taggen
eule report                                  # Journal-Gesamtreport
eule report --setup ep --days 90             # Nur EP-Trades der letzten 90 Tage
eule stats                                   # Win Rate, R-Multiples, Equity Curve
eule check                                   # Konsistenz: Journal vs. Broker-Positionen
```

---

## Hase-Trades: Primärquellen (LIES DIESE SELBST!)

Hase speichert Trades in PostgreSQL. Eule soll diese Daten direkt lesen können.
**Kopiere keine Schemas — lies die Originale:**

| Was | Pfad |
|-----|------|
| DB-Verbindung + Trade-Queries | `~/fin/hase/hase/elster/data.py` (`get_db_connection`, `load_trades`) |
| Environment-Config (DATABASE_URL) | `~/fin/hase/run/real/ibkr-one/.env`, `~/fin/hase/run/real/ibkr-two/.env` |
| Runtime-Config (settle time etc.) | `~/fin/hase/run/real/ibkr-one/config.json` |
| Elster Metriken (wiederverwendbar) | `~/fin/hase/hase/elster/metrics.py` |
| ShadowPortfolio (mark_to_market, expire_options) | `~/fin/hase/hase/popodienst/shadowportfolio.py` |
| Precheck/Monitoring | `~/fin/hase/monitoring/` |

**Hinweise zur Roundtrip-Erkennung** (entdeckt am 01.04.2026 bei manueller DB-Analyse):
- Entry = `sell` mit `trade_ref` (echte Broker-Execution)
- Expiry = `buy` mit `price=0.0`, `trade_ref=NULL` (synthetisch von `expire_options()`)
- Grouping: Entry+Exit bilden einen Roundtrip pro `strategy_key` + Datum-Sequenz
- Erster Trade (02.03.) hat vermutlich einen Multiplier-Bug (`value=2.45` statt `245.00`)

**Vorgeschlagene CLI-Befehle:**

```bash
eule trades --env real-ibkr                              # Alle Trades
eule trades --env real-ibkr --strategy spx-0dte-mon-put  # Gefiltert
eule trades --env real-ibkr --days 30                    # Zeitfenster
eule trades --env real-ibkr --json                       # Maschinenlesbar für Wachtel
```

---

## Datenmodelle

### Position (Phase 1)

```python
class Position:
    id: str                     # eindeutige ID
    broker: str                 # ibkr, tr, tradier, willbe
    ticker: str                 # Symbol
    name: str                   # Unternehmensname
    asset_type: str             # stock, etf, option, bond, gold_physical, gold_etc
    direction: str              # long / short
    size: float                 # Anzahl Shares/Contracts/Gramm
    entry_price: float          # Kaufkurs
    entry_date: date            # Kaufdatum
    current_price: float        # Aktueller Kurs (live oder cached)
    currency: str               # USD, EUR
    unrealized_pnl: float       # in Originalwährung
    unrealized_pnl_eur: float   # konvertiert
    category: str               # core, opportunistic, gold, bond, speculative
    thesis: str                 # Freitext: warum gekauft
    exit_criteria: list[str]    # Liste von Exit-Triggern
    notes: str                  # Freitext
```

### Option (Phase 1, extends Position)

```python
class OptionPosition:
    underlying: str             # Underlying Ticker
    strike: float
    expiry: date
    option_type: str            # call / put
    sold_premium: float         # kassierte Prämie bei Verkauf
    current_value: float        # aktueller Optionspreis
    pnl_percent: float          # (sold - current) / sold
    fifty_pct_target: float     # 50% der sold_premium → Schließen
    days_to_expiry: int
```

### Bond (Phase 1, extends Position)

```python
class BondPosition:
    issuer: str                 # Emittent
    coupon_rate: float          # Kuponzins
    coupon_frequency: str       # annual, semi-annual, quarterly
    maturity_date: date
    face_value: float           # Nennwert
    credit_rating: str          # BBB-, BB+, etc.
    next_coupon_date: date
    annual_income: float        # Nennwert × Kuponzins
```

### Trade (Phase 2)

```python
class Trade:
    id: str                     # eindeutige ID
    broker: str
    ticker: str
    asset_class: str            # stock, option, future, crypto, cfd, bond, etf
    direction: str              # long / short
    size: float
    entry_price: float
    entry_date: datetime
    exit_price: float | None    # null wenn noch offen
    exit_date: datetime | None
    stop_price: float | None    # für R-Multiple
    currency: str
    pnl: float | None           # realisierter P&L
    pnl_eur: float | None
    r_multiple: float | None
    setup_tag: str | None       # EP, RS, Earnings, Hase, SharpeTwo, CSP, CC, B&H, Dip-Buy
    error_tag: str | None       # early_exit, no_stop, fomo, oversize, revenge, ondo_moment
    thesis: str | None
    notes: str | None
    # Für Optionen:
    strike: float | None
    expiry: date | None
    option_type: str | None     # call/put
    underlying: str | None
```

---

## Abgrenzung zu Elster

Elster = Hase-internes Tages-Monitoring (Sharpe, DD, PF pro Strategie pro Tag).
Eule = alle Broker, Trade-Ebene, Positions-Übersicht, Optionen/Bonds, News.
**Lies Elster selbst:** `~/fin/hase/hase/elster/` (besonders `data.py`, `metrics.py`).
Eule kann Elsters `PerformanceMetrics` Dataclass wiederverwenden.

---

## Storage

SQLite als primärer Storage (flexibel, query-fähig, single file).
Datenbank-File: `~/.eule/trades.db` (oder konfigurierbar).
Config-File: `~/.eule/config.yaml`

Config enthält:
- Broker-Credentials Referenzen (Pfade zu .env Files)
- Basiswährung: EUR
- Zielallokation (core: 65%, opportunistic: 20%, gold: 10%, bonds: 15%)
- Manuelle Positionen (Trade Republic, Willbe Gold)
- Thesis-Referenz-Datei (positions-bh.md Pfad)

### Technische Rahmenbedingungen

- Python 3.12+
- Poetry für Dependency Management
- Typer für CLI
- SQLite via SQLAlchemy oder direkt sqlite3 (für Eule-eigene Daten)
- PostgreSQL-Lesezugriff auf Hase-DB (psycopg, KEIN SQLAlchemy nötig — Hase nutzt psycopg direkt)
- Kein Web-Frontend (CLI only, Reports als Markdown)
- Muss auf Linux laufen (Tower + systematic Server)
- Credentials in separaten .env Files (nicht im Repo)
- **Deployment:** Entwicklung auf Tower, Produktion auf systematic (wie Hase/Hamster)
- **Hase-DB-Zugriff:** DATABASE_URL aus Hase Environment-Config wiederverwendbar (kein Doppelpflege)
- **JSON-Output:** Jeder CLI-Befehl MUSS `--json` / `--format json` unterstützen (für Wachtel-Integration via SSH)

---

## Was die Spezifikation enthalten soll

1. **Datenmodell** — SQLAlchemy Models oder Schema-Definition (Position, Option, Bond, Trade)
2. **Positions-Aggregator Interface** — einheitlich für Live-Positionen pro Broker
3. **Manueller Positions-Config** — für TR, Willbe (kein API)
4. **Options-Tracker** — 50%-Regel, Verfall, Roll-Logik
5. **Bond-Tracker** — Kupon, Fälligkeit, Rating
6. **Allocation-Checker** — Soll/Ist mit Kategorien
7. **Monitor-Module** — News, Vol, Earnings, Bond-Emittenten
8. **Briefing-Generator** — Zusammenfassendes Markdown
9. **CLI-Struktur** — Commands und Subcommands
10. **Config-Schema** — was in config.yaml stehen muss
11. **Phase 2 Erweiterungspunkte** — wo Trade-Journal später andockt

### Phase 1 Implementierungs-Reihenfolge

**Schritt 0 — Hase-Trades (schnellster Mehrwert, Daten schon da):**
0. `eule trades` — Hase PostgreSQL-Import + Roundtrip-Erkennung + CLI

**Schritt 1-9 — Positions + Monitor:**
1. Config + manuelle Positionen (TR, Willbe)
2. IBKR Positions-Abfrage (via Hase API)
3. Tradier Positions-Abfrage (REST API)
4. Positions-Aggregator + `eule positions`
5. Options-Tracker + `eule options`
6. Bond-Tracker
7. Allocation-Checker + `eule allocation`
8. Monitor/Briefing + `eule briefing`
9. Thesis-Checker + `eule thesis`
