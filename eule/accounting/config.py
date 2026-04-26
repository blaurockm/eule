"""Loader fuer tradingGbr/config.yaml.

Default-Pfad: ~/Dokumente/obsidian/tradingGbr/config.yaml
Override via Umgebungsvariable EULE_TRADINGGBR_DIR.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class AccountingConfigError(Exception):
    """Fehler beim Laden der Accounting-Config."""


def tradinggbr_dir() -> Path:
    """Verzeichnis mit config.yaml, cash.yaml, tokens.yaml."""
    override = os.environ.get("EULE_TRADINGGBR_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Dokumente" / "obsidian" / "tradingGbr"


@dataclass(frozen=True)
class HolderDef:
    id: str               # "A" oder "B"
    name: str
    capital_share: float  # 0.0 - 1.0


@dataclass(frozen=True)
class PerformanceFee:
    pct: float                  # z.B. 0.10 fuer 10%
    base: str                   # "per_winning_roundtrip" (aktuell einziger Modus)
    recipient: str              # holder.id


@dataclass(frozen=True)
class AccountingConfig:
    env: str                              # "real2-ibkr"
    base_currency: str
    holders: list[HolderDef]
    operator: str                         # holder.id
    performance_fee: PerformanceFee
    fiscal_year_start: str                # "01-01"
    balances_json_path: str               # Ziel-Pfad fuer Vercel-App-JSON

    def holder(self, holder_id: str) -> HolderDef:
        for h in self.holders:
            if h.id == holder_id:
                return h
        raise AccountingConfigError(f"Holder '{holder_id}' nicht in config gefunden")


def load_accounting_config(path: Path | None = None) -> AccountingConfig:
    """Laedt config.yaml aus tradingGbr-Verzeichnis."""
    if path is None:
        path = tradinggbr_dir() / "config.yaml"
    path = path.expanduser()

    if not path.exists():
        raise AccountingConfigError(
            f"Config nicht gefunden: {path}\n"
            f"Lege sie an oder setze EULE_TRADINGGBR_DIR."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    try:
        account = raw["account"]
        env = account["env"]
        base_currency = account.get("base_currency", "EUR")

        holders = [
            HolderDef(id=h["id"], name=h["name"], capital_share=float(h["capital_share"]))
            for h in raw["holders"]
        ]
        operator = raw["operator"]

        pf = raw["performance_fee"]
        fee = PerformanceFee(
            pct=float(pf["pct"]),
            base=pf.get("base", "per_winning_roundtrip"),
            recipient=pf["recipient"],
        )

        fiscal_year_start = raw.get("fiscal_year_start", "01-01")
        balances_json = raw.get("output", {}).get("balances_json", "")

    except KeyError as e:
        raise AccountingConfigError(f"Pflichtfeld fehlt in {path}: {e}") from e

    cfg = AccountingConfig(
        env=env,
        base_currency=base_currency,
        holders=holders,
        operator=operator,
        performance_fee=fee,
        fiscal_year_start=fiscal_year_start,
        balances_json_path=balances_json,
    )

    _validate(cfg)
    return cfg


def _validate(cfg: AccountingConfig) -> None:
    if len(cfg.holders) != 2:
        raise AccountingConfigError(
            f"Genau zwei Holder erwartet, gefunden: {len(cfg.holders)}"
        )
    holder_ids = {h.id for h in cfg.holders}
    if cfg.operator not in holder_ids:
        raise AccountingConfigError(
            f"Operator '{cfg.operator}' ist kein Holder ({holder_ids})"
        )
    if cfg.performance_fee.recipient not in holder_ids:
        raise AccountingConfigError(
            f"Fee-Recipient '{cfg.performance_fee.recipient}' ist kein Holder"
        )
    total_share = sum(h.capital_share for h in cfg.holders)
    if abs(total_share - 1.0) > 1e-9:
        raise AccountingConfigError(
            f"Summe der capital_share ist {total_share}, erwartet 1.0"
        )
    if cfg.performance_fee.base != "per_winning_roundtrip":
        raise AccountingConfigError(
            f"performance_fee.base='{cfg.performance_fee.base}' "
            f"nicht implementiert (nur 'per_winning_roundtrip')"
        )
