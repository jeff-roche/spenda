from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spenda.db import database
from spenda.ingestion.claude import discover_claude_home, ingest_claude
import spenda.ingestion.claude as claude_module
from spenda.pricing import reprice_usage
from spenda.reports import session_detail


@dataclass
class _Settings:
    database: Path
    claude_home: Path
    running_window_seconds: int = 0

    def validate(self):
        return self


def _line(*, kind: str, session: str, timestamp: str, **extra) -> str:
    value = {"type": kind, "sessionId": session, "timestamp": timestamp, **extra}
    return json.dumps(value, separators=(",", ":"))


def _assistant(
    *, session: str, message_id: str, timestamp: str, input_tokens: int, output_tokens: int,
    git_branch: str | None = None, effort: str | None = None, content: object | None = None,
) -> str:
    metadata = {}
    if git_branch is not None:
        metadata["gitBranch"] = git_branch
    if effort is not None:
        metadata["effort"] = effort
    return _line(
        kind="assistant", session=session, timestamp=timestamp, cwd="/work/repo", version="2.1.263",
        **metadata,
        message={
            "id": message_id, "model": "claude-test", "role": "assistant",
            # The text must never be copied into dashboard fields.
            "content": "private assistant response" if content is None else content,
            "usage": {
                "input_tokens": input_tokens, "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 3, "output_tokens": output_tokens,
                "output_tokens_details": {"thinking_tokens": 3},
            },
        },
    )


def _fixture(home: Path, *, cost_state: str = "complete") -> tuple[Path, Path]:
    root = home / "projects" / "-work-repo" / "root.jsonl"
    child = home / "projects" / "-work-repo" / "root" / "subagents" / "agent-child.jsonl"
    child.parent.mkdir(parents=True)
    root.parent.mkdir(parents=True, exist_ok=True)
    records = [
            _assistant(session="root", message_id="message-root", timestamp="2026-09-01T10:00:00Z", input_tokens=1, output_tokens=1, git_branch="main", effort="high"),
            # Streaming snapshots reuse the same message ID.  The final row is
            # the only accounting record that should remain.
            _assistant(session="root", message_id="message-root", timestamp="2026-09-01T10:00:01Z", input_tokens=4, output_tokens=5, git_branch="main", effort="high"),
            _line(kind="ai-title", session="root", timestamp="2026-09-01T10:00:01Z", aiTitle="Generated safe title"),
    ]
    if cost_state != "missing":
        records.append(
            _line(
                kind="cost-state", session="root", timestamp="2026-09-01T10:00:02Z",
                startTime=1788256802000, totalCostUSD=1.5,
                modelUsage={"claude-test": {"costUSD": 1.5}},
                hasUnknownModelCost=cost_state == "unknown",
            )
        )
    root.write_text("\n".join(records) + "\n", encoding="utf-8")
    child.write_text(
        _assistant(session="root", message_id="message-child", timestamp="2026-09-01T10:00:03Z", input_tokens=6, output_tokens=7, effort="low") + "\n",
        encoding="utf-8",
    )
    return root, child


def test_claude_ingestion_keeps_only_accounting_and_latest_message(tmp_path):
    home = tmp_path / "claude"
    root, child = _fixture(home)
    source_bytes = {root: root.read_bytes(), child: child.read_bytes()}
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    summary = ingest_claude(settings)

    assert discover_claude_home(settings) == home.resolve()
    assert (summary.root_sessions, summary.subagent_sessions, summary.usage_records) == (1, 1, 3)
    assert summary.unknown_prices == {"anthropic:claude-test"}
    with database(settings.database, readonly=True) as conn:
        paths = dict(conn.execute("SELECT thread_id,agent_path FROM agents"))
        usage = conn.execute(
            "SELECT input_tokens,cached_input_tokens,cache_write_input_tokens,uncached_input_tokens,"
            "output_tokens,reasoning_output_tokens,total_tokens,cost_usd FROM usage "
            "WHERE source_record_identity='claude:root:root:message-root'"
        ).fetchone()
        cost = conn.execute(
            "SELECT cost_usd,total_tokens,timestamp,source_file FROM usage WHERE source_event_type='claude_cost_state'"
        ).fetchone()
        session = conn.execute(
            "SELECT turn_count,first_user_message_preview,source_app,source_version,git_branch,root_reasoning_effort FROM sessions"
        ).fetchone()
        detail = session_detail(conn, "claude:root")
        cost_label = conn.execute(
            "SELECT turn_id,response_id,call_label FROM usage WHERE source_event_type='claude_cost_state'"
        ).fetchone()

    assert paths == {
        "claude:root": "/root",
        "claude:root:agent:agent-child": "/root/agent-child",
    }
    assert tuple(usage) == (9, 2, 3, 4, 5, 3, 14, "0")
    assert tuple(cost) == ("1.5", 0, "2026-09-01T10:00:02Z", str(home / "projects" / "-work-repo" / "root.jsonl"))
    assert tuple(session) == (2, None, "claude", "2.1.263", "main", "high")

    with database(settings.database, readonly=True) as conn:
        efforts = dict(conn.execute("SELECT thread_id,reasoning_effort FROM agents"))
    assert efforts == {"claude:root": "high", "claude:root:agent:agent-child": "low"}
    assert detail["usage_events"] == 2
    assert tuple(cost_label) == (None, None, "Cumulative cost state")
    assert {path: path.read_bytes() for path in source_bytes} == source_bytes
    with sqlite3.connect(settings.database) as conn:
        logical_dump = "\n".join(conn.iterdump())
    assert "private assistant response" not in logical_dump

    with database(settings.database) as conn:
        before = conn.execute(
            "SELECT source_event_type,cost_usd,pricing_note FROM usage WHERE source_event_type GLOB 'claude_*' ORDER BY id"
        ).fetchall()
        assert reprice_usage(conn, provider="anthropic") == 0
        after = conn.execute(
            "SELECT source_event_type,cost_usd,pricing_note FROM usage WHERE source_event_type GLOB 'claude_*' ORDER BY id"
        ).fetchall()
    assert before == after

    second = ingest_claude(settings)
    assert (second.usage_records, second.duplicate_records) == (0, 3)


def test_claude_derives_fixed_action_labels_without_persisting_content(tmp_path):
    home = tmp_path / "claude"
    root = home / "projects" / "-work-repo" / "root.jsonl"
    root.parent.mkdir(parents=True)
    tool_snapshot = _assistant(
        session="root", message_id="tool-message", timestamp="2026-09-01T10:00:00Z",
        input_tokens=1, output_tokens=1,
        content=[
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "tool_use", "name": "Edit", "input": {
                "file_path": "/private/file", "new_string": "private replacement"
            }},
        ],
    )
    # Streaming may replace the content blocks while retaining the message ID;
    # keep the strongest safe label while using the latest accounting values.
    final_snapshot = _assistant(
        session="root", message_id="tool-message", timestamp="2026-09-01T10:00:01Z",
        input_tokens=2, output_tokens=2,
        content=[{"type": "text", "text": "private final response"}],
    )
    text_message = _assistant(
        session="root", message_id="text-message", timestamp="2026-09-01T10:00:02Z",
        input_tokens=3, output_tokens=3,
        content=[{"type": "text", "text": "another private response"}],
    )
    root.write_text("\n".join((tool_snapshot, final_snapshot, text_message)) + "\n", encoding="utf-8")
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        labels = dict(conn.execute(
            "SELECT source_record_identity,call_label FROM usage "
            "WHERE source_event_type='claude_assistant_message' ORDER BY source_record_identity"
        ))
        latest_tokens = conn.execute(
            "SELECT input_tokens,output_tokens FROM usage "
            "WHERE source_record_identity='claude:root:root:tool-message'"
        ).fetchone()
    assert labels == {
        "claude:root:root:text-message": "Assistant response",
        "claude:root:root:tool-message": "Apply file change",
    }
    assert tuple(latest_tokens) == (7, 2)
    with sqlite3.connect(settings.database) as conn:
        logical_dump = "\n".join(conn.iterdump())
    for private_value in (
        "private reasoning", "/private/file", "private replacement",
        "private final response", "another private response",
    ):
        assert private_value not in logical_dump


@pytest.mark.parametrize(
    ("cost_state", "note"),
    (
        ("missing", "no cumulative cost-state"),
        ("unknown", "unknown model cost"),
    ),
)
def test_claude_marks_missing_or_unknown_cost_state_partial(tmp_path, cost_state, note):
    home = tmp_path / "claude"
    _fixture(home, cost_state=cost_state)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    summary = ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        calls = conn.execute(
            "SELECT cost_usd FROM usage WHERE source_event_type='claude_assistant_message' ORDER BY id"
        ).fetchall()
        session = conn.execute("SELECT accounting_status,accounting_note,title FROM sessions").fetchone()
    assert [row[0] for row in calls] == [None, None]
    assert session[0] == "partial" and note in session[1]
    assert session[2] == "Generated safe title"
    assert summary.unknown_prices == {"anthropic:claude-test"}


def test_claude_reconciles_removed_subagent_and_preserves_codex(tmp_path):
    home = tmp_path / "claude"
    _, child = _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)
    with database(settings.database) as conn:
        conn.execute(
            "INSERT INTO sessions(id,root_thread_id,source_app,source_home) VALUES(?,?,?,?)",
            ("codex:kept", "codex:kept", "codex", "/tmp/codex"),
        )
        conn.execute(
            "INSERT INTO agents(thread_id,session_id,agent_role,source_kind) VALUES(?,?,?,?)",
            ("codex:kept", "codex:kept", "root", "cli"),
        )

    child.unlink()
    summary = ingest_claude(settings)

    assert (summary.root_sessions, summary.subagent_sessions) == (1, 0)
    with database(settings.database, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agents WHERE source_kind='claude'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM usage WHERE source_event_type='claude_assistant_message'").fetchone()[0] == 1
        assert conn.execute("SELECT turn_count FROM sessions WHERE id='claude:root'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id='codex:kept'").fetchone()[0] == 1


def test_complete_root_cost_state_leaves_subagent_usage_unpriced(tmp_path):
    home = tmp_path / "claude"
    _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    summary = ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        costs = dict(conn.execute(
            "SELECT thread_id,cost_usd FROM usage WHERE source_event_type='claude_assistant_message'"
        ))
        session = conn.execute(
            "SELECT accounting_status,accounting_note FROM sessions WHERE id='claude:root'"
        ).fetchone()
    assert costs == {"claude:root": "0", "claude:root:agent:agent-child": None}
    assert tuple(session) == (
        "partial", "Claude Code root cost-state does not prove coverage of subagent transcript usage"
    )
    assert summary.unknown_prices == {"anthropic:claude-test"}


def test_missing_projects_preserves_claude_rows_but_readable_empty_projects_reconcile(tmp_path):
    home = tmp_path / "claude"
    _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)

    projects = home / "projects"
    projects.rename(home / "projects-unavailable")
    unavailable = ingest_claude(settings)
    assert unavailable.parser_warnings == 1
    with database(settings.database, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source_app='claude'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM usage WHERE source_record_identity LIKE 'claude:%'").fetchone()[0] == 3

    projects.mkdir()
    empty = ingest_claude(settings)
    assert empty.parser_warnings == 0
    with database(settings.database, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source_app='claude'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM usage WHERE source_record_identity LIKE 'claude:%'").fetchone()[0] == 0


def test_read_failure_skips_reconciliation_and_keeps_existing_claude_rows(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    root, _ = _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)
    with database(settings.database, readonly=True) as conn:
        before_session = conn.execute(
            "SELECT title,cwd,created_at,updated_at,status,accounting_status,accounting_note "
            "FROM sessions WHERE id='claude:root'"
        ).fetchone()
        before_agent = conn.execute(
            "SELECT agent_path,created_at,updated_at,model,reasoning_effort "
            "FROM agents WHERE thread_id='claude:root'"
        ).fetchone()

    original_open = Path.open

    def failed_open(path, *args, **kwargs):
        if path == root:
            raise OSError("simulated transcript read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(claude_module.Path, "open", failed_open)
    summary = ingest_claude(settings)
    assert summary.parser_warnings == 1
    with database(settings.database, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage WHERE source_record_identity LIKE 'claude:%'").fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM usage WHERE source_record_identity='claude:root:root:message-root'"
        ).fetchone()[0] == 1
        after_session = conn.execute(
            "SELECT title,cwd,created_at,updated_at,status,accounting_status,accounting_note "
            "FROM sessions WHERE id='claude:root'"
        ).fetchone()
        after_agent = conn.execute(
            "SELECT agent_path,created_at,updated_at,model,reasoning_effort "
            "FROM agents WHERE thread_id='claude:root'"
        ).fetchone()
    assert after_session == before_session
    assert after_agent == before_agent


def test_initial_partial_scan_cannot_mark_root_cost_coverage_complete(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    _, child = _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    original_open = Path.open

    def failed_open(path, *args, **kwargs):
        if path == child:
            raise OSError("simulated child read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(claude_module.Path, "open", failed_open)
    summary = ingest_claude(settings)

    assert summary.parser_warnings == 1
    with database(settings.database, readonly=True) as conn:
        session = conn.execute(
            "SELECT accounting_status,accounting_note FROM sessions WHERE id='claude:root'"
        ).fetchone()
    assert tuple(session) == (
        "partial", "Claude Code scan has not established complete cost coverage"
    )


def test_claude_liveness_uses_latest_subagent_timestamp(tmp_path):
    home = tmp_path / "claude"
    _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home, running_window_seconds=5)

    ingest_claude(settings, now=datetime(2026, 9, 1, 10, 0, 5, tzinfo=UTC))
    with database(settings.database, readonly=True) as conn:
        running = conn.execute(
            "SELECT status,updated_at,finished_at FROM sessions WHERE id='claude:root'"
        ).fetchone()
    assert tuple(running) == ("running", "2026-09-01T10:00:03Z", None)

    ingest_claude(settings, now=datetime(2026, 9, 1, 10, 0, 9, tzinfo=UTC))
    with database(settings.database, readonly=True) as conn:
        completed = conn.execute(
            "SELECT status,updated_at,finished_at FROM sessions WHERE id='claude:root'"
        ).fetchone()
    assert tuple(completed) == (
        "completed", "2026-09-01T10:00:03Z", "2026-09-01T10:00:03Z"
    )


def test_claude_cost_state_replaces_removed_models_and_preserves_exact_total(tmp_path):
    home = tmp_path / "claude"
    root = home / "projects" / "-work-repo" / "root.jsonl"
    root.parent.mkdir(parents=True)
    assistant = _assistant(
        session="root", message_id="message-root", timestamp="2026-09-01T10:00:00Z",
        input_tokens=1, output_tokens=1,
    )
    initial = _line(
        kind="cost-state", session="root", timestamp="2026-09-01T10:00:01Z",
        totalCostUSD=3, modelUsage={"claude-a": {"costUSD": 1}, "claude-b": {"costUSD": 2}},
        hasUnknownModelCost=False,
    )
    root.write_text(f"{assistant}\n{initial}\n", encoding="utf-8")
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)

    replacement = _line(
        kind="cost-state", session="root", timestamp="2026-09-01T10:00:02Z",
        totalCostUSD=4, modelUsage={"claude-a": {"costUSD": 4}}, hasUnknownModelCost=False,
    )
    root.write_text(f"{assistant}\n{replacement}\n", encoding="utf-8")
    ingest_claude(settings)
    with database(settings.database, readonly=True) as conn:
        rows = conn.execute(
            "SELECT model,cost_usd FROM usage WHERE source_event_type='claude_cost_state' ORDER BY model"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("claude-a", "4")]

    inconsistent = _line(
        kind="cost-state", session="root", timestamp="2026-09-01T10:00:03Z",
        totalCostUSD=1, modelUsage={"claude-a": {"costUSD": 0.7}, "claude-b": {"costUSD": 0.7}},
        hasUnknownModelCost=False,
    )
    root.write_text(f"{assistant}\n{inconsistent}\n", encoding="utf-8")
    ingest_claude(settings)
    with database(settings.database, readonly=True) as conn:
        rows = conn.execute(
            "SELECT model,cost_usd FROM usage WHERE source_event_type='claude_cost_state' ORDER BY model"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("claude-code-cumulative-total", "1")]


def test_claude_cost_snapshots_preserve_historical_daily_changes(tmp_path):
    home = tmp_path / "claude"
    root = home / "projects" / "-work-repo" / "root.jsonl"
    root.parent.mkdir(parents=True)
    snapshots = (
        _line(
            kind="cost-state", session="root", timestamp="2026-09-01T10:00:00Z",
            totalCostUSD=10, modelUsage={"claude-test": {"costUSD": 10}},
            hasUnknownModelCost=False,
        ),
        _line(
            kind="cost-state", session="root", timestamp="2026-09-02T10:00:00Z",
            totalCostUSD=15, modelUsage={"claude-test": {"costUSD": 15}},
            hasUnknownModelCost=False,
        ),
    )
    root.write_text("\n".join(snapshots) + "\n", encoding="utf-8")
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        daily = conn.execute(
            "SELECT date(timestamp),SUM(CAST(cost_usd AS REAL)) FROM usage "
            "WHERE source_event_type='claude_cost_state' GROUP BY date(timestamp) ORDER BY 1"
        ).fetchall()
    assert [tuple(row) for row in daily] == [
        ("2026-09-01", 10.0),
        ("2026-09-02", 5.0),
    ]


def test_complete_root_reconciles_costs_when_another_transcript_is_malformed(tmp_path):
    home = tmp_path / "claude"
    root, child = _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)

    replacement = _line(
        kind="cost-state", session="root", timestamp="2026-09-02T10:00:00Z",
        totalCostUSD=4, modelUsage={"claude-new": {"costUSD": 4}},
        hasUnknownModelCost=False,
    )
    root.write_text(replacement + "\n", encoding="utf-8")
    child.write_text("{malformed\n", encoding="utf-8")

    ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        costs = conn.execute(
            "SELECT model,cost_usd FROM usage WHERE source_event_type='claude_cost_state'"
        ).fetchall()
    assert [tuple(row) for row in costs] == [("claude-new", "4")]


def test_malformed_line_keeps_valid_assistant_records(tmp_path):
    home = tmp_path / "claude"
    root = home / "projects" / "-work-repo" / "root.jsonl"
    root.parent.mkdir(parents=True)
    root.write_text(
        _assistant(
            session="root", message_id="first", timestamp="2026-09-01T10:00:00Z",
            input_tokens=1, output_tokens=1,
        )
        + "\n{malformed\n"
        + _assistant(
            session="root", message_id="second", timestamp="2026-09-01T10:01:00Z",
            input_tokens=2, output_tokens=2,
        )
        + "\n",
        encoding="utf-8",
    )
    settings = _Settings(tmp_path / "dashboard.sqlite", home)

    summary = ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        calls = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE source_event_type='claude_assistant_message'"
        ).fetchone()[0]
    assert summary.malformed_lines == 1
    assert calls == 2


def test_unreadable_project_does_not_erase_claude_history(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    root, _ = _fixture(home)
    settings = _Settings(tmp_path / "dashboard.sqlite", home)
    ingest_claude(settings)
    original_scandir = claude_module.os.scandir

    def unreadable_project(path):
        if Path(path) == root.parent:
            raise PermissionError("simulated unreadable project")
        return original_scandir(path)

    monkeypatch.setattr(claude_module.os, "scandir", unreadable_project)
    summary = ingest_claude(settings)

    with database(settings.database, readonly=True) as conn:
        sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE source_app='claude'"
        ).fetchone()[0]
    assert summary.parser_warnings == 1
    assert sessions == 1
