"""
DB-Verbindung zu Hase PostgreSQL.

Liest DATABASE_URL direkt aus den Hase .env-Dateien — kein Hase-Import noetig.
"""

import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values

# Hase-Environments: env-name -> (config_dir, runtime_name aus config.json)
HASE_BASE = Path.home() / "fin" / "hase"

ENV_DIRS: dict[str, Path] = {
    "real-ibkr": HASE_BASE / "run" / "real" / "ibkr-one",
    "real2-ibkr": HASE_BASE / "run" / "real" / "ibkr-two",
}


def _load_env_file(env_dir: Path) -> dict[str, str | None]:
    """Liest .env-Datei aus Hase-Environment-Verzeichnis."""
    env_file = env_dir / ".env"
    if not env_file.exists():
        raise FileNotFoundError(f".env nicht gefunden: {env_file}")
    return dotenv_values(env_file)


def get_runtime_name(env_dir: Path) -> str:
    """Liest runtime_name aus config.json des Hase-Environments."""
    config_file = env_dir / "config.json"
    if not config_file.exists():
        raise FileNotFoundError(f"config.json nicht gefunden: {config_file}")
    with open(config_file) as f:
        cfg = json.load(f)
    return cfg["runtime_name"]


def get_db_connection(env_name: str) -> psycopg.Connection:
    """
    Oeffnet DB-Verbindung fuer ein Hase-Environment.

    Args:
        env_name: z.B. "real-ibkr" oder "real2-ibkr"

    Returns:
        psycopg Connection

    Raises:
        ValueError: Unbekanntes Environment
        FileNotFoundError: .env oder config.json fehlt
        RuntimeError: DATABASE_URL nicht in .env
    """
    if env_name not in ENV_DIRS:
        available = ", ".join(sorted(ENV_DIRS.keys()))
        raise ValueError(f"Unbekanntes Environment: '{env_name}'. Verfuegbar: {available}")

    env_dir = ENV_DIRS[env_name]
    values = _load_env_file(env_dir)
    db_url = values.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(f"DATABASE_URL nicht in {env_dir / '.env'}")

    return psycopg.connect(db_url, autocommit=True)


def get_env_info(env_name: str) -> tuple[psycopg.Connection, str]:
    """
    Gibt DB-Connection und runtime_name fuer ein Environment zurueck.

    Returns:
        (connection, runtime_name)
    """
    env_dir = ENV_DIRS[env_name]
    conn = get_db_connection(env_name)
    runtime_name = get_runtime_name(env_dir)
    return conn, runtime_name


def list_environments() -> list[str]:
    """Gibt verfuegbare Environment-Namen zurueck."""
    return sorted(ENV_DIRS.keys())
