import sqlite3

import pytest

from config import Settings


def test_railway_without_volume_refuses_ephemeral_database(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    settings = Settings(_env_file=None, DB_PATH=str(tmp_path / "damas_reys.db"))

    with pytest.raises(RuntimeError, match="Railway Volume topilmadi"):
        settings.get_db_path()


def test_railway_relative_database_path_is_resolved_inside_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    settings = Settings(_env_file=None, DB_PATH="damas_reys.db")

    assert settings.get_db_path() == str(tmp_path / "damas_reys.db")


def test_railway_rejects_database_path_outside_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    settings = Settings(_env_file=None, DB_PATH=str(tmp_path.parent / "ephemeral.db"))

    with pytest.raises(RuntimeError, match="DB_PATH Railway Volume ichida"):
        settings.get_db_path()


def test_railway_refuses_missing_or_invalid_existing_database(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    settings = Settings(_env_file=None)
    missing_path = tmp_path / "missing.db"

    with pytest.raises(RuntimeError, match="mavjud baza topilmadi"):
        settings.validate_existing_database(str(missing_path))

    db_path = tmp_path / "unrelated.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE something_else (id INTEGER)")
    with pytest.raises(RuntimeError, match="baza yaroqsiz yoki bot bazasi emas"):
        settings.validate_existing_database(str(db_path))


def test_railway_accepts_existing_valid_bot_database(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    settings = Settings(_env_file=None)
    db_path = tmp_path / "valid.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE students (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO students DEFAULT VALUES")

    settings.validate_existing_database(str(db_path))
