import pytest

from memory_engine.config import DB_FILE, Settings, default_db_path


def test_db_path_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "env.lbdb"))
    assert Settings.from_env().db_path == str(tmp_path / "env.lbdb")
    assert Settings.from_env(db_path=tmp_path / "cli.lbdb").db_path == str(tmp_path / "cli.lbdb")
    monkeypatch.delenv("MEMORY_DB_PATH")
    assert Settings.from_env().db_path == str(default_db_path())


def test_default_db_path_is_per_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # honoured on Linux; macOS/Windows use their own dirs
    path = default_db_path()
    assert path.name == DB_FILE and path.parent.name == "memory-engine"


def test_backend_default_and_override(monkeypatch):
    assert Settings.from_env().backend == "ladybug"
    monkeypatch.setenv("MEMORY_BACKEND", "Neo4j")
    assert Settings.from_env().backend == "neo4j"
    assert Settings.from_env(backend="ladybug").backend == "ladybug"
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="Unknown MEMORY_BACKEND"):
        Settings.from_env()
