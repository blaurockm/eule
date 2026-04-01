# CLAUDE.md

## Projekt

Eule 🦉 — Trade Journal + Portfolio Monitor CLI. Teil des Trading-Ökosystems (Hase, Hamster, Dachs, Igel, Fuchs, Elster).

## Spezifikation

Lies `eule-spec-prompt.md` in diesem Repo — das ist die vollständige Anforderung.

## Referenz-Dateien (auf diesem Server)

| Was | Pfad |
|-----|------|
| Hase DB-Zugriff + Trade-Queries | `~/fin/hase/hase/elster/data.py` |
| Hase Environment-Configs | `~/fin/hase/run/real/ibkr-one/.env`, `~/fin/hase/run/real/ibkr-two/.env` |
| Hase Runtime-Config | `~/fin/hase/run/real/ibkr-one/config.json` |
| Elster Metriken (wiederverwendbar) | `~/fin/hase/hase/elster/metrics.py` |
| ShadowPortfolio | `~/fin/hase/hase/popodienst/shadowportfolio.py` |
| Positionen + Thesen | `~/fin/trading-collab/positions-bh.md` |
| B&H Playbook | `~/fin/trading-collab/buy-and-hold-playbook.md` |
| Backlog + offene Fragen | `~/fin/trading-collab/skills-backlog.md` (Abschnitt 5) |

**Lies die Originale, kopiere keine Schemas.** Wenn du das DB-Schema brauchst, inspiziere `data.py` und die DB direkt.

## Regeln

- Python 3.12+, Poetry, Typer CLI
- **Jeder CLI-Befehl MUSS `--json` unterstützen** (für Wachtel-Integration via SSH)
- Kein LLM in Eule — nur deterministische Datenverarbeitung
- Tests schreiben (pytest)
- Nicht raten. Originale lesen.

## Implementierungs-Reihenfolge

**Phase 1 — Schritt 0 (jetzt):**
1. Poetry-Projekt init mit CLI Entry Point (`eule`)
2. `eule trades --env real-ibkr` — Hase PostgreSQL-Import + Roundtrip-Erkennung
3. Tests
4. Commit + Push

Danach Phase 1 Schritt 1-9 (Positions, Options, Bonds, Allocation, Briefing) — aber erstmal nur Schritt 0.
