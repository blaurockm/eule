# Backlog: Reconciliation-Watchdog (Hase-DB vs. IBKR Flex)

Status: Backlog-Skizze (2026-07-04). Noch nicht implementiert.

## Motivation

Vorfall 2026-06-22: Hase buchte die auslaufenden SPXW-Puts zu Preis 0
(WS-Cache lieferte SPX 7500.58, echtes Settlement 7472.79). Das ITM-Settlement
(real2 −2163 USD, real-ibkr −1442 USD) fehlte komplett in `daily_pnl` — der
Weekly-Report zeigte Gewinn statt Verlust. Aufgefallen ist es nur durch
manuellen Abgleich mit dem IBKR-Statement-of-Funds. Spiegelbildlich dazu
2026-06-15: Phantom-Verlust (~19k über beide Accounts) durch stale Settle-Preis.

Der Hase-Fix (`37e7fa7`, datierter History-Bar statt WS-Cache) reduziert das
Risiko, aber: Daily-Close ≠ offizielles Settlement (knappe Fälle bleiben
kritisch), und Buchungsfehler anderer Art (fehlende Legs, Fee-Semantik) fängt
er nicht. Eule bekommt deshalb eine zweite Verteidigungslinie: deterministischer
T+1-Abgleich der Hase-DB gegen die authoritativen Broker-Cashflows.

## Ziel

`eule reconcile` vergleicht pro Environment und Handelstag die Hase-DB
(`daily_pnl`, `trades`) mit dem IBKR-Statement-of-Funds (Flex Web Service)
und alarmiert über Wachtel (Telegram + Email) bei Differenzen.

## Datenquellen

| Quelle | Zugriff | Status |
|--------|---------|--------|
| Hase-DB (`daily_pnl`, `trades`) | `EULE_DB_*` (eine DB, Filter `runtime_name`) | vorhanden |
| SoF real2-ibkr | Flex Web Service (`EULE_IBKR_FLEX_TOKEN` + `EULE_IBKR_FLEX_QUERY_ID`), Cache `tradingGbr/sof/` | vorhanden (`eule accounting fetch`) |
| SoF real-ibkr | eigene Flex-Query nötig | **fehlt** — Query in IBKR Account Management anlegen, neue Env-Vars (z.B. `EULE_IBKR_FLEX_QUERY_ID_REAL_IBKR`), eigener Cache (nicht tradingGbr — das ist GbR-Buchhaltung) |

Parser wiederverwenden: `eule.accounting.import_sof` (SoF-CSV → Rows mit
Klassifikation trade/transfer/fee). FX pro Posten aus dem SoF selbst
(`FXRateToBase` bzw. Amount/TradeGross-Verhältnis) — kein externer FX-Feed.

## Checks (pro Env, pro Tag)

1. **Tages-PnL-Abgleich** — Summe der SoF-Trade-Cashflows (USD) vs.
   `daily_pnl.pnl_realized` (Summe über Strategien). Toleranz konfigurierbar,
   z.B. `max(5 USD, 0.5%)`. Differenz → WARNING, > 50 USD → CRITICAL.
2. **Expiry-/Settlement-Check** (der 22.06.-Fall) — für jeden Tag mit
   Options-Expiry: SoF-Settlement-Posten (AssetClass=OPT, TradePrice=0,
   Amount≠0) müssen einen korrespondierenden Hase-Expiry-Trade
   (`trade_ref LIKE 'EXP_%'`) mit Preis ≈ |Amount|/(qty×100) haben.
   Settlement im SoF, aber Hase-Trade zu Preis 0 → CRITICAL.
3. **Leg-Vollständigkeit** — Anzahl SoF-Options-Posten (ohne Settlements) vs.
   Anzahl Hase-Trades pro Tag. Am 22.06. fehlte in real-ibkr der komplette
   Short-Leg-Buyback in `trades`.
4. **Fee-Konsistenz** — Verdacht aus dem Abgleich 2026-07-04: `pnl_net =
   pnl_realized − fees`, obwohl `pnl_realized` bereits broker-netto ist
   (Doppelabzug ~18 EUR/Woche, kumuliert ~272 EUR seit März). Check:
   `pnl_realized` (USD, ×FX) sollte den SoF-Nettobeträgen entsprechen —
   wenn ja, ist der Fees-Abzug in `pnl_net` doppelt → einmalig in Hase klären.
5. **NAV-Plausibilität** — `__TOTAL__.nav_end`-Tagessprünge minus `pnl_net`
   minus bekannte Transfers (SoF AssetClass='' ≥ 100) sollten ≈ 0 sein.
   Fängt stille NAV-Drops ohne PnL (wie 24.06.).

## CLI

```
eule reconcile --env real2-ibkr [--days 7 | --date 2026-06-22] --format json
```

- Default: letzte 7 Kalendertage, alle Envs mit Flex-Query.
- `--format json` (Pflicht-Regel für Wachtel-Integration): Liste von Findings
  `{env, date, check, severity, expected, actual, diff, detail}`.
- Exit-Code 0 = sauber, 1 = Findings (für Cron/Precheck-Nutzung).

## Scheduling (Wachtel)

- Job in `schedule_config.py`: werktags Di–Sa vormittags (Flex-Statement für
  Vortag ist ~morgens verfügbar; Samstag prüft Freitag). Achtung: Flex-Fetch
  ist am Wochenende unzuverlässig → Retry-Logik bzw. Sa-Lauf mit Fallback
  auf Cache.
- `notify: [telegram, email]` nur bei Findings; `on_error` immer.
- Kein LLM, rein deterministisch (Eule-Grundregel).

## Implementierungsschritte

1. Flex-Query für real-ibkr anlegen (manuell, IBKR Account Management) +
   Env-Vars + Fetch-Pfad pro Env verallgemeinern (`eule.accounting.fetch`
   von tradingGbr entkoppeln: SoF-Cache-Verzeichnis pro Env konfigurierbar).
2. Modul `eule/reconcile/` mit den Checks 1–3 und 5 (Check 4 ist einmalige
   Klärung in Hase, danach Assertion hier).
3. CLI-Command + Tests (Fixtures: SoF-CSV-Ausschnitte vom 15./22.06. als
   Regressionsfälle — die realen Incident-Daten liegen in
   `tradingGbr/sof/sof-current.csv`).
4. Schedule-Job + Wachtel-Rendering (Telegram kurz, Email mit Tabelle).

## Referenzen

- Incident-Analyse 2026-07-04 (Session-Notizen): SPXW-Settlement 7472.79,
  WS-Cache 7500.58, DB-Korrekturen dokumentiert in den Backup-Tabellen
  `daily_pnl_backup_20260704`, `trades_backup_20260704`,
  `strategy_cash_backup_20260704` (gemeinsame Hase-DB).
- Hase-Fix: Commit `37e7fa7` (fix(settlement): derive expiry settle price
  from dated history bar) — Stand 04.07. noch nicht auf systematic deployt.
