# Drift-Monitoring fuer silbern-marder Credit Spreads

Stand: 2026-07-12 (DACHS Session 2026-07-12-A). Ansprechpartner-Doku:
`dachs/research/silbern-marder/journal.md`, Sessions 2026-07-12 und 2026-07-12-A.

## Problem

Die Hase-Configs der SPX-Credit-Spreads verwenden **feste Punkt- und
Dollar-Werte** (`spread_distance: 25`, `sell_premium: 2.50`). Der SPX hat sich
seit 2022 fast verdoppelt — feste Werte aendern dadurch schleichend ihre
Bedeutung:

- **Hedge-Breite:** 25pt waren 2022 = 0.60% vom Index, heute = 0.35%. Der
  Hedge wird prozentual enger, teurer und zahlt oefter, ohne dass jemand das
  entschieden hat. Hedge-Kosten 2026 sind ~3x der 4-Jahres-Mediane.
- **Premium-Anker:** $2.50 kauft je nach Index/Vol eine andere Moneyness
  (2022: 1.25% OTM, 2024: 0.50%, 2026: 0.94%). Die Vol-Adaption ist
  erwuenscht (fixe Moneyness wurde getestet und ist klar schlechter,
  Sharpe 0.8 vs 2.0), aber der langsame Index-Drift soll ueberwacht werden.

Entscheidung 2026-07-12: Werte bleiben fix (keine %-Definition in Hase),
dafuer wird der Drift ueberwacht und bei Bedarf bewusst nachgezogen.

## Referenzwerte (Kalibrierung 2026-07-12)

| Strategie | Parameter | Ist | Semantik-Ziel | Warn-Schwelle |
|-----------|-----------|-----|---------------|---------------|
| spx-0dte-mon-put(-small) | spread_distance | 25pt | 0.35% vom SPX | Abw. >15% |
| spx-0dte-tue-put | spread_distance | 10pt | 0.135% vom SPX | Abw. >15% |
| spx-0dte-mon-put(-small) | sell_premium | $2.50 | 0.036% vom Spot | Abw. >25% |
| spx-0dte-tue-put | sell_premium | $4.00 | 0.058% vom Spot | Abw. >25% |
| Montag OTM-Band | — | — | Short in [0.4%, 1.5%] OTM | Band-Austritt |

Beispiel Hedge-Drift: SPX bei 8600 -> 25pt = 0.29% (-17%) -> WARN mit
Vorschlag `spread_distance: 30`.

## Integration in Wachtel/Eule (Vorschlag)

### Stufe A — Baseline-Feld (billig, sofort umsetzbar)

Der Hedge-Breiten-Check braucht nur die Hase-Config + aktuellen SPX-Kurs
(beides hat Eule bereits: `EULE_HASE_DIR` + quotes). Vorschlag fuer die
Baseline-YAMLs (`eule/monitoring/baselines/spx-0dte-*.yaml`):

```yaml
drift:
  hedge_width_pct:
    target: 0.35          # spread_distance / SPX-Spot * 100
    warn_deviation: 0.15  # relative Abweichung
```

Precheck-Erweiterung: `spread_distance` aus der Strategie-JSON lesen,
durch aktuellen SPX teilen, gegen `target` pruefen, bei WARN einen
gerundeten Punkte-Vorschlag ausgeben (5pt-Raster).

### Stufe B — Jahres-Review (DACHS-Script, Datenfenster)

**Randbedingung (2026-07):** Das Massive-Abo (Polygon Flat-Files,
`options_intraday`) ist gekuendigt. Frische Optionsdaten gibt es nur noch
in einem bewussten Datenfenster: einmal jaehrlich 1 Monat Massive abonnieren,
Flat-Files fuer die Luecke backfillen (Massive liefert waehrend des Abos
die komplette Historie — beim ersten Re-Abo verifizieren!), Hamster-Import,
dann das Review-Script laufen lassen.

`dachs/research/silbern-marder/review_drift.py`:

1. Hedge-Breiten-Drift (wie Stufe A, alle Strategien) — laeuft jederzeit
2. Premium-Anker: OTM% + Premium/Spot der letzten 26 Entries
   (Spot parity-implizit aus der Chain) — nur im Datenfenster aktuell
3. Breiten-Sweep letzte 24 Monate: Credit/Verlierer/Cap-Durchschlaege je
   Uniform-Breite; WARN wenn >=2 Verlierer in 12M die aktive Breite
   durchschlagen — nur im Datenfenster aktuell

Exit-Code 1 bei Warnungen. Ein Cron lohnt damit nicht mehr — das Script
ist Teil der jaehrlichen Review-Routine. Kontinuierlich laufen nur
Stufe A (Wachtel) und perspektivisch Stufe C.

### Stufe C — Fill-basierte Checks (Hase-Fix umgesetzt 2026-07-12)

Zwischen den Datenfenstern speisen sich Premium-Anker- und
Cap-Durchschlag-Check aus den **Live-Trades selbst** (jede Woche ein
Datenpunkt: gewaehlter Strike vs. Spot bei Entry, Settlement-Tiefe vs.
Hedge-Breite).

Der Hase-Fix ist umgesetzt (Root Cause: Combo-Legs teilten sich die
Broker-Order-ID, der Hedge-Entry wurde per ON-CONFLICT-Dedupe verworfen;
`trade_ref` ist jetzt `trade_id|order_id`; neues Feld `underlying_price`
auf Order/Trade):

- Hedge-Entry-Fill wird gebucht -> Netto-Credit aus der DB korrekt
  (vorher ~3x ueberzeichnet am Di, ~1.5x am Mo)
- Underlying-Kurs bei Entry -> Premium/Spot-Drift direkt aus Fills

**Wirksam erst fuer Trades nach dem Deploy** — historische Zeilen bleiben
ohne Hedge-Entry. Eule-Auswertungen muessen Alt-Zeitraeume entsprechend
behandeln (Netto-Credit vor Deploy-Datum ist Brutto).

**Noch zu verifizieren** (fuer OTM%- und Cap-Durchschlag-Check): ob die
`symbol`-Spalte jetzt den Leg-Kontraktnamen (mit Strike) traegt oder
weiterhin nur den Universe-Key `spx_opt` (`t.key or t.product.name` —
haengt davon ab, was am Leg-Trade gesetzt ist). Ohne Strike in der DB
bleibt fuer diese beiden Checks nur das Jahres-Datenfenster (Stufe B).

## Warum keine %-Definition direkt in Hase?

Untersucht und bewusst verworfen bzw. vertagt (Session 2026-07-12-A):

- `spread_distance_pct` waere sauber stationaer, macht aber den
  Dollar-Max-Loss index-abhaengig (Portfolio-Entscheidung).
- Premium als fixe Moneyness (OTM%) ist **empirisch widerlegt** — das
  Dollar-Premium ist der implizite Vol-Adapter der Strategie (Regel-C-Test:
  Sharpe 0.82 vs 2.00).
- Drift-Monitoring + bewusstes Nachziehen liefert 90% des Nutzens ohne
  Hase-Aenderung.
