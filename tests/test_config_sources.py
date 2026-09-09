from __future__ import annotations

import sqlite3

import pytest

from spenda.config import Settings
from spenda.db import SCHEMA, SCHEMA_VERSION, initialize


def test_v3_sessions_migrate_to_generic_source_columns(tmp_path):
    path = tmp_path / "dashboard.sqlite"
    v3_schema = SCHEMA.replace(
        "    source_app TEXT NOT NULL DEFAULT 'codex',\n"
        "    source_home TEXT NOT NULL,\n"
        "    source_version TEXT,",
        "    source_codex_home TEXT NOT NULL,\n"
        "    source_codex_version TEXT,",
    ).replace(
        "CREATE INDEX IF NOT EXISTS idx_sessions_source_created ON sessions(source_app, created_at);\n",
        "",
    )
    with sqlite3.connect(path) as conn:
        conn.executescript(v3_schema)
        conn.execute(
            "INSERT INTO sessions(id, root_thread_id, source_codex_home, source_codex_version) "
            "VALUES ('session-1', 'thread-1', '/tmp/codex', '0.1.0')"
        )
        conn.execute("INSERT INTO dashboard_meta(key, value) VALUES ('schema_version', '3')")

    initialize(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        row = conn.execute(
            "SELECT source_app, source_home, source_version FROM sessions WHERE id='session-1'"
        ).fetchone()
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(sessions)")}
        version = conn.execute(
            "SELECT value FROM dashboard_meta WHERE key='schema_version'"
        ).fetchone()[0]

    assert {"source_app", "source_home", "source_version"} <= columns
    assert "source_codex_home" not in columns
    assert "source_codex_version" not in columns
    assert row == ("codex", "/tmp/codex", "0.1.0")
    assert "idx_sessions_source_created" in indexes
    assert version == str(SCHEMA_VERSION)


def test_opencode_database_uses_environment_override(monkeypatch, tmp_path):
    source = tmp_path / "opencode.sqlite"
    monkeypatch.setenv("OPENCODE_DB", str(source))

    settings = Settings.load(tmp_path / "codex", tmp_path / "dashboard.sqlite")

    assert settings.opencode_database == source.resolve()


def test_claude_home_uses_environment_override(monkeypatch, tmp_path):
    source = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(source))

    settings = Settings.load(tmp_path / "codex", tmp_path / "dashboard.sqlite")

    assert settings.claude_home == source.resolve()


def test_dashboard_database_cannot_equal_opencode_source(tmp_path):
    path = tmp_path / "shared.sqlite"
    settings = Settings(tmp_path / "codex", path, opencode_database=path)

    with pytest.raises(ValueError, match="OpenCode source"):
        settings.validate()


def test_dashboard_database_cannot_be_inside_claude_home(tmp_path):
    claude_home = tmp_path / "claude"
    settings = Settings(tmp_path / "codex", claude_home / "dashboard.sqlite", claude_home=claude_home)

    with pytest.raises(ValueError, match="CLAUDE_CONFIG_DIR"):
        settings.validate()
