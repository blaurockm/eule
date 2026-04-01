"""Tests fuer DB-Modul (ohne echte DB-Verbindung)."""

import os

import pytest

from eule.db import _build_env_dirs, _get_hase_base, ENV_DIRS, list_environments


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


class TestHaseBase:
    def test_default_hase_base(self, monkeypatch):
        """Ohne EULE_HASE_DIR: Fallback auf ~/fin/hase."""
        monkeypatch.delenv("EULE_HASE_DIR", raising=False)
        from pathlib import Path

        base = _get_hase_base()
        assert base == Path.home() / "fin" / "hase"

    def test_custom_hase_base(self, monkeypatch):
        """EULE_HASE_DIR ueberschreibt den Pfad."""
        monkeypatch.setenv("EULE_HASE_DIR", "/tmp/my-hase")
        from pathlib import Path

        base = _get_hase_base()
        assert base == Path("/tmp/my-hase")

    def test_build_env_dirs_uses_base(self, monkeypatch):
        """_build_env_dirs nutzt den konfigurierten Basispfad."""
        monkeypatch.setenv("EULE_HASE_DIR", "/opt/hase")
        from pathlib import Path

        dirs = _build_env_dirs()
        assert dirs["real-ibkr"] == Path("/opt/hase/run/real/ibkr-one")
        assert dirs["real2-ibkr"] == Path("/opt/hase/run/real/ibkr-two")


class TestDbConnectionErrors:
    def test_unknown_env_raises(self):
        from eule.db import get_db_connection

        with pytest.raises(ValueError, match="Unbekanntes Environment"):
            get_db_connection("nonexistent-env")
