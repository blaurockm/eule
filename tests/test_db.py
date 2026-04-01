"""Tests fuer DB-Modul (ohne echte DB-Verbindung)."""

import pytest

from eule.db import ENV_DIRS, list_environments


class TestEnvConfig:
    def test_list_environments(self):
        envs = list_environments()
        assert "real-ibkr" in envs
        assert "real2-ibkr" in envs

    def test_env_dirs_exist(self):
        """Prüfe dass die konfigurierten Hase-Verzeichnisse existieren."""
        for env_name, env_dir in ENV_DIRS.items():
            assert env_dir.exists(), f"Hase-Verzeichnis fehlt fuer {env_name}: {env_dir}"
            assert (env_dir / ".env").exists(), f".env fehlt fuer {env_name}"
            assert (env_dir / "config.json").exists(), f"config.json fehlt fuer {env_name}"


class TestDbConnectionErrors:
    def test_unknown_env_raises(self):
        from eule.db import get_db_connection

        with pytest.raises(ValueError, match="Unbekanntes Environment"):
            get_db_connection("nonexistent-env")
