"""
DB-Verbindung zu Hase PostgreSQL.

Liest DATABASE_URL aus Umgebungsvariablen (EULE_DB_REAL_IBKR etc.).
"""

import os

import psycopg


# Environment → Umgebungsvariable fuer DATABASE_URL
_ENV_DB_VARS: dict[str, str] = {
    "real-ibkr": "EULE_DB_REAL_IBKR",
    "real2-ibkr": "EULE_DB_REAL2_IBKR",
    "staging-ibkr": "EULE_DB_STAGING_IBKR",
    "staging-hl": "EULE_DB_STAGING_HL",
}

# Environment → runtime_name (fester DB-Filter, aendert sich nicht)
RUNTIME_NAMES: dict[str, str] = {
    "real-ibkr": "ibkr-one",
    "real2-ibkr": "ibkr-two",
    "staging-ibkr": "ibkr-paper",
    "staging-hl": "hl-paper",
}


def get_db_url(env_name: str) -> str:
    """DATABASE_URL fuer ein Environment aus Umgebungsvariable lesen."""
    if env_name not in _ENV_DB_VARS:
        available = ", ".join(sorted(_ENV_DB_VARS.keys()))
        raise ValueError(f"Unbekanntes Environment: '{env_name}'. Verfuegbar: {available}")

    var_name = _ENV_DB_VARS[env_name]
    url = os.environ.get(var_name)
    if not url:
        raise RuntimeError(
            f"Umgebungsvariable {var_name} nicht gesetzt. "
            f"Setze sie in ~/.eule/.env oder als Export."
        )
    return url


def get_db_connection(env_name: str) -> psycopg.Connection:
    """Oeffnet DB-Verbindung fuer ein Hase-Environment."""
    return psycopg.connect(get_db_url(env_name), autocommit=True)


def get_env_info(env_name: str) -> tuple[psycopg.Connection, str]:
    """
    Gibt DB-Connection und runtime_name fuer ein Environment zurueck.

    Returns:
        (connection, runtime_name)
    """
    conn = get_db_connection(env_name)
    runtime_name = RUNTIME_NAMES[env_name]
    return conn, runtime_name


def list_environments() -> list[str]:
    """Gibt verfuegbare Environment-Namen zurueck."""
    return sorted(_ENV_DB_VARS.keys())
