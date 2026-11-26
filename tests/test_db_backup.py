"""DB-Backup-Job: Rotation, Fehlerpfade, kein Secret auf der Kommandozeile."""

import subprocess

import pytest

from eule.monitoring import db_backup

SECRET_URL = "postgresql://user:geheim@host:5432/db"


@pytest.fixture(autouse=True)
def _db_url(monkeypatch):
    monkeypatch.setattr(db_backup, "get_db_url", lambda env: SECRET_URL)


def _fake_run(payload: bytes, returncode: int = 0, stderr: bytes = b""):
    calls = []

    def run(cmd, stdout, stderr, env, timeout):  # noqa: A002
        calls.append({"cmd": cmd, "env": env})
        stdout.write(payload)
        return subprocess.CompletedProcess(cmd, returncode, stderr=stderr_bytes)

    stderr_bytes = stderr
    run.calls = calls
    return run


def test_backup_writes_dump_and_keeps_secret_out_of_argv(tmp_path, monkeypatch):
    run = _fake_run(b"x" * 200_000)
    monkeypatch.setattr(db_backup.subprocess, "run", run)

    target = db_backup.run_backup(tmp_path)

    assert target.exists() and target.stat().st_size == 200_000
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    call = run.calls[0]
    assert all("geheim" not in part for part in call["cmd"])
    assert call["env"]["PGURL"] == SECRET_URL


def test_backup_fails_on_pg_dump_error(tmp_path, monkeypatch):
    monkeypatch.setattr(db_backup.subprocess, "run", _fake_run(b"", returncode=1, stderr=b"connection refused"))

    with pytest.raises(RuntimeError, match="connection refused"):
        db_backup.run_backup(tmp_path)
    assert not list(tmp_path.iterdir())


def test_backup_fails_on_implausibly_small_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(db_backup.subprocess, "run", _fake_run(b"tiny"))

    with pytest.raises(RuntimeError, match="unplausibel klein"):
        db_backup.run_backup(tmp_path)
    assert not list(tmp_path.iterdir())


def test_rotate_keeps_newest(tmp_path):
    for day in range(1, 6):
        (tmp_path / f"hase-db-2026-11-0{day}.dump").write_bytes(b"d")
    (tmp_path / "other.txt").write_text("bleibt")

    removed = db_backup.rotate(tmp_path, keep=2)

    assert sorted(p.name for p in removed) == [f"hase-db-2026-11-0{d}.dump" for d in (1, 2, 3)]
    assert sorted(p.name for p in tmp_path.glob("*.dump")) == ["hase-db-2026-11-04.dump", "hase-db-2026-11-05.dump"]
    assert (tmp_path / "other.txt").exists()


def test_job_is_registered():
    from eule.monitoring.jobs import INTERNAL_JOBS

    assert "db_backup" in INTERNAL_JOBS
