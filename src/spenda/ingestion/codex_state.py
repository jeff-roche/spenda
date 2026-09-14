from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class StateSnapshot:
    threads: list[dict[str, Any]]
    edges: dict[str, str]
    schema_version: int | None
    state_path: Path | None
    warning: str | None = None


def _utc(value: Any, milliseconds: bool = False) -> str | None:
    if value is None:
        return None
    try:
        number = float(value) / (1000 if milliseconds else 1)
        return datetime.fromtimestamp(number, UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def find_state_database(codex_home: Path) -> Path | None:
    candidates = list(codex_home.glob("state_*.sqlite"))
    if not candidates:
        return None

    def generation(path: Path) -> tuple[int, int]:
        try:
            number = int(path.stem.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            number = -1
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return number, modified
    return max(candidates, key=generation)


def _structured_parent(source: Any) -> tuple[str | None, dict[str, Any]]:
    try:
        value = json.loads(source) if isinstance(source, str) and source.startswith("{") else source
        spawn = value["subagent"]["thread_spawn"]
        return spawn.get("parent_thread_id"), spawn
    except (KeyError, TypeError, json.JSONDecodeError):
        return None, {}


def read_state(codex_home: Path) -> StateSnapshot:
    path = find_state_database(codex_home)
    if path is None:
        return StateSnapshot([], {}, None, None, "state database not found")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "threads" not in tables:
            conn.close()
            return StateSnapshot([], {}, None, path, "unsupported state schema: no threads table")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(threads)")}
        rows: list[dict[str, Any]] = []
        for raw in conn.execute("SELECT * FROM threads"):
            item = dict(raw)
            parent, spawn = _structured_parent(item.get("source"))
            item["structured_parent"] = parent
            item["structured_spawn"] = spawn
            item["created_iso"] = _utc(
                item.get("created_at_ms") if "created_at_ms" in columns else item.get("created_at"),
                "created_at_ms" in columns and item.get("created_at_ms") is not None,
            )
            item["updated_iso"] = _utc(
                item.get("updated_at_ms") if "updated_at_ms" in columns else item.get("updated_at"),
                "updated_at_ms" in columns and item.get("updated_at_ms") is not None,
            )
            rows.append(item)
        edges: dict[str, str] = {}
        if "thread_spawn_edges" in tables:
            edges.update({r[1]: r[0] for r in conn.execute(
                "SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges"
            )})
        for item in rows:
            if item["structured_parent"]:
                edges.setdefault(item["id"], item["structured_parent"])
        version = None
        if "_sqlx_migrations" in tables:
            found = conn.execute("SELECT MAX(version) FROM _sqlx_migrations WHERE success=1").fetchone()
            version = found[0] if found else None
        conn.close()
        return StateSnapshot(rows, edges, version, path)
    except sqlite3.Error as exc:
        log.warning("Could not read Codex state database %s: %s", path, exc)
        return StateSnapshot([], {}, None, path, str(exc))


def resolve_root(thread_id: str, parents: dict[str, str]) -> tuple[str, bool]:
    current = thread_id
    seen = {current}
    while current in parents:
        current = parents[current]
        if current in seen:
            return thread_id, True
        seen.add(current)
    return current, False
