"""
Taeglicher Dump der Hase-Datenbank (Supabase, Free-Tarif ohne eigene Backups).

Laeuft als Scheduler-Job (schedule.yaml: function db_backup). pg_dump kommt aus
dem Docker-Image postgres:17 — auf systematic ist kein Postgres-Client
installiert. Die Verbindungs-URL geht per Umgebungsvariable in den Container,
nicht ueber die Kommandozeile.
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger as log

from eule.config import EULE_DIR
from eule.db import get_db_url

BACKUP_DIR = EULE_DIR / "backups"
BACKUP_ENV = "real2-ibkr"
PG_IMAGE = "postgres:17"
KEEP_DUMPS = 14
MIN_DUMP_BYTES = 100_000
DUMP_TIMEOUT_S = 600
_PREFIX = "hase-db-"
_SUFFIX = ".dump"


def run_backup(backup_dir: Path = BACKUP_DIR, keep: int = KEEP_DUMPS) -> Path:
    """Schreibt einen pg_dump (custom format, Schema public) und rotiert alte Dumps.

    Raises:
        RuntimeError: pg_dump fehlgeschlagen oder Dump unplausibel klein.
    """
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    date_str = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")
    target = backup_dir / f"{_PREFIX}{date_str}{_SUFFIX}"
    tmp = target.with_suffix(".tmp")

    cmd = [
        "docker", "run", "--rm", "--network", "host", "-e", "PGURL", PG_IMAGE,
        "sh", "-c", 'pg_dump "$PGURL" -n public -Fc',
    ]
    try:
        with open(tmp, "wb") as out:
            proc = subprocess.run(
                cmd,
                stdout=out,
                stderr=subprocess.PIPE,
                env={**os.environ, "PGURL": get_db_url(BACKUP_ENV)},
                timeout=DUMP_TIMEOUT_S,
            )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()[-300:]
            raise RuntimeError(f"pg_dump exit {proc.returncode}: {stderr}")
        size = tmp.stat().st_size
        if size < MIN_DUMP_BYTES:
            raise RuntimeError(f"Dump unplausibel klein ({size} Bytes)")
        os.chmod(tmp, 0o600)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)

    removed = rotate(backup_dir, keep)
    log.info(f"DB-Backup geschrieben: {target} ({size} Bytes), {len(removed)} alte Dumps entfernt")
    return target


def rotate(backup_dir: Path, keep: int) -> list[Path]:
    """Behaelt die `keep` neuesten Dumps (nach Dateiname = Datum), loescht den Rest."""
    dumps = sorted(backup_dir.glob(f"{_PREFIX}*{_SUFFIX}"))
    stale = dumps[:-keep] if keep > 0 else dumps
    for path in stale:
        path.unlink()
    return stale
