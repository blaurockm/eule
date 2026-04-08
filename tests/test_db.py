"""Tests fuer DB-Modul (ohne echte DB-Verbindung)."""

import pytest

from eule.db import RUNTIME_NAMES, get_db_url, list_environments


class TestEnvConfig:
    def test_list_environments(self):
        envs = list_environments()
        assert "real-ibkr" in envs
        assert "real2-ibkr" in envs

    def test_runtime_names_complete(self):
        """Jedes Environment hat einen runtime_name."""
        for env in list_environments():
            assert env in RUNTIME_NAMES


class TestGetDbUrl:
    def test_unknown_env_raises(self):
        with pytest.raises(ValueError, match="Unbekanntes Environment"):
            get_db_url("nonexistent-env")

    def test_missing_env_var_raises(self, monkeypatch):
        """Fehlende Umgebungsvariable gibt klare Fehlermeldung."""
        monkeypatch.delenv("EULE_DB_REAL_IBKR", raising=False)
        with pytest.raises(RuntimeError, match="EULE_DB_REAL_IBKR"):
            get_db_url("real-ibkr")

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("EULE_DB_REAL_IBKR", "postgresql://test:5432/db")
        assert get_db_url("real-ibkr") == "postgresql://test:5432/db"


class TestDbConnectionErrors:
    def test_unknown_env_raises(self):
        from eule.db import get_db_connection

        with pytest.raises(ValueError, match="Unbekanntes Environment"):
            get_db_connection("nonexistent-env")
