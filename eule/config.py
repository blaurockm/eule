"""
Config-System fuer Eule.

Laedt ~/.eule/config.yaml + Broker-spezifische .env-Dateien.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values


EULE_DIR = Path.home() / ".eule"
CONFIG_PATH = EULE_DIR / "config.yaml"


class ConfigError(Exception):
    """Fehler beim Laden oder Validieren der Config."""


@dataclass(frozen=True)
class BrokerConfig:
    """Konfiguration eines einzelnen Brokers."""

    name: str
    enabled: bool = True
    broker_type: str = ""  # ibkr, tradier, ig, manual
    env_file: str = ""
    positions_file: str = ""
    base_url: str = ""
    extra: dict = field(default_factory=dict)

    def load_env(self) -> dict[str, str | None]:
        """Laedt .env-Datei fuer diesen Broker. Gibt leeres Dict wenn nicht konfiguriert."""
        if not self.env_file:
            return {}
        path = Path(self.env_file).expanduser()
        if not path.exists():
            raise ConfigError(f"Broker {self.name}: env_file nicht gefunden: {path}")
        return dotenv_values(path)


@dataclass(frozen=True)
class AllocationTarget:
    """Ziel-Allokation fuer eine Kategorie."""

    category: str
    min_pct: float
    max_pct: float


@dataclass(frozen=True)
class AllocationConfig:
    """Allokations-Konfiguration."""

    targets: list[AllocationTarget] = field(default_factory=list)
    max_single_position_pct: float = 0.15


@dataclass(frozen=True)
class AlertsConfig:
    """Alert-Schwellenwerte."""

    option_expiry_warning_days: list[int] = field(default_factory=lambda: [7, 3, 1])
    fifty_pct_rule: bool = True
    earnings_warning_days: int = 14


@dataclass(frozen=True)
class EuleConfig:
    """Haupt-Konfiguration."""

    base_currency: str = "EUR"
    brokers: dict[str, BrokerConfig] = field(default_factory=dict)
    allocation: AllocationConfig = field(default_factory=AllocationConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    thesis_file: str = ""


def _parse_broker(name: str, raw: dict) -> BrokerConfig:
    """Parsed eine Broker-Konfiguration aus dem YAML dict."""
    broker_type = raw.get("type", "")
    # Typ aus Name ableiten wenn nicht explizit
    if not broker_type:
        if name.startswith("ibkr"):
            broker_type = "ibkr"
        elif name == "tradier":
            broker_type = "tradier"
        elif name == "ig":
            broker_type = "ig"
        else:
            broker_type = "manual"

    return BrokerConfig(
        name=name,
        enabled=raw.get("enabled", True),
        broker_type=broker_type,
        env_file=raw.get("env_file", ""),
        positions_file=raw.get("positions_file", ""),
        base_url=raw.get("base_url", ""),
        extra={k: v for k, v in raw.items()
               if k not in ("enabled", "type", "env_file", "positions_file", "base_url")},
    )


def _parse_allocation(raw: dict) -> AllocationConfig:
    """Parsed Allokations-Config."""
    targets = []
    for cat, vals in raw.get("targets", {}).items():
        targets.append(AllocationTarget(
            category=cat,
            min_pct=vals.get("min", 0.0),
            max_pct=vals.get("max", 1.0),
        ))
    return AllocationConfig(
        targets=targets,
        max_single_position_pct=raw.get("max_single_position_pct", 0.15),
    )


def _parse_alerts(raw: dict) -> AlertsConfig:
    """Parsed Alert-Config."""
    return AlertsConfig(
        option_expiry_warning_days=raw.get("option_expiry_warning_days", [7, 3, 1]),
        fifty_pct_rule=raw.get("fifty_pct_rule", True),
        earnings_warning_days=raw.get("earnings_warning_days", 14),
    )


def load_config(path: Path | None = None) -> EuleConfig:
    """Laedt die Eule-Konfiguration aus YAML.

    Args:
        path: Pfad zur config.yaml. Default: ~/.eule/config.yaml
    """
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Config nicht gefunden: {config_path}\n"
            "Erstelle mit: eule config init"
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    brokers = {}
    for name, broker_raw in raw.get("brokers", {}).items():
        if isinstance(broker_raw, dict):
            brokers[name] = _parse_broker(name, broker_raw)

    allocation = _parse_allocation(raw.get("allocation", {}))
    alerts = _parse_alerts(raw.get("alerts", {}))

    return EuleConfig(
        base_currency=raw.get("base_currency", "EUR"),
        brokers=brokers,
        allocation=allocation,
        alerts=alerts,
        thesis_file=raw.get("thesis_file", ""),
    )


CONFIG_TEMPLATE = """\
# Eule Config — ~/.eule/config.yaml

base_currency: EUR

brokers:
  ibkr-one:
    enabled: true
    type: ibkr
    env_file: "~/.eule/ibkr-one.env"

  ibkr-two:
    enabled: false
    type: ibkr
    env_file: "~/.eule/ibkr-two.env"

  tradier:
    enabled: false
    env_file: "~/.eule/tradier.env"
    base_url: "https://api.tradier.com/v1"

  ig:
    enabled: false
    env_file: "~/.eule/ig.env"

  trade_republic:
    enabled: true
    type: manual
    positions_file: "~/.eule/tr-positions.yaml"

  willbe:
    enabled: true
    type: manual
    positions_file: "~/.eule/willbe-positions.yaml"

allocation:
  targets:
    core: { min: 0.55, max: 0.70 }
    opportunistic: { min: 0.15, max: 0.30 }
    gold: { min: 0.05, max: 0.15 }
    bonds: { min: 0.05, max: 0.25 }
  max_single_position_pct: 0.15

alerts:
  option_expiry_warning_days: [7, 3, 1]
  fifty_pct_rule: true
  earnings_warning_days: 14

thesis_file: "~/fin/trading-collab/positions-bh.md"
"""

ENV_TEMPLATES = {
    "ibkr-one.env": """\
# IBKR Credentials (Kopie aus Hase)
IBIND_ACCOUNT_ID=
IBIND_USE_OAUTH=True
IBIND_OAUTH1A_CONSUMER_KEY=
IBIND_OAUTH1A_ENCRYPTION_KEY_FP=
IBIND_OAUTH1A_SIGNATURE_KEY_FP=
IBIND_OAUTH1A_ACCESS_TOKEN=
IBIND_OAUTH1A_ACCESS_TOKEN_SECRET=
IBIND_OAUTH1A_DH_PRIME=
""",
    "ibkr-two.env": """\
# IBKR Account 2 Credentials
IBIND_ACCOUNT_ID=
IBIND_USE_OAUTH=True
IBIND_OAUTH1A_CONSUMER_KEY=
IBIND_OAUTH1A_ENCRYPTION_KEY_FP=
IBIND_OAUTH1A_SIGNATURE_KEY_FP=
IBIND_OAUTH1A_ACCESS_TOKEN=
IBIND_OAUTH1A_ACCESS_TOKEN_SECRET=
IBIND_OAUTH1A_DH_PRIME=
""",
    "tradier.env": """\
# Tradier Credentials
TRADIER_TOKEN=
TRADIER_ACCOUNT_ID=
""",
    "ig.env": """\
# IG Markets Credentials
IG_USERNAME=
IG_PASSWORD=
IG_API_KEY=
IG_ACC_NUMBER=
""",
}

MANUAL_POSITION_TEMPLATE = """\
# Positionen fuer {broker}
# Jede Position als Eintrag in der Liste.

positions:
  - ticker: "EXAMPLE"
    name: "Beispiel-Position"
    asset_type: stock    # stock, etf, bond, gold_physical, gold_etc
    direction: long
    size: 10
    entry_price: 100.0
    currency: EUR
    category: core       # core, opportunistic, gold, bonds
    # entry_date: "2025-01-15"  # optional

    # Fuer Anleihen zusaetzlich:
    # issuer: "Emittent"
    # coupon_rate: 0.05
    # coupon_frequency: annual  # annual, semi-annual, quarterly
    # maturity_date: "2026-08-15"
    # face_value: 1000.0
    # credit_rating: "BBB"
"""


def init_config() -> list[str]:
    """Erstellt Config-Templates unter ~/.eule/. Gibt Liste erstellter Dateien zurueck."""
    EULE_DIR.mkdir(parents=True, exist_ok=True)
    created = []

    # config.yaml
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(CONFIG_TEMPLATE)
        created.append(str(CONFIG_PATH))

    # .env Templates
    for filename, content in ENV_TEMPLATES.items():
        path = EULE_DIR / filename
        if not path.exists():
            path.write_text(content)
            created.append(str(path))

    # Manuelle Positions-Templates
    for broker in ("tr-positions.yaml", "willbe-positions.yaml"):
        path = EULE_DIR / broker
        if not path.exists():
            broker_name = broker.replace("-positions.yaml", "").upper()
            path.write_text(MANUAL_POSITION_TEMPLATE.format(broker=broker_name))
            created.append(str(path))

    return created
