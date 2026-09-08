from __future__ import annotations

import json
from pathlib import Path

from codex_dashboard.db import database
from codex_dashboard.config import Settings
from codex_dashboard.ingestion.scanner import ingest
from conftest import atomic, make_state, session_meta, thread, token_count, turn, usage_values, write_rollout


def setup_root(settings, records=None):
    path = settings.codex_home / "sessions" / "2026" / "09" / "08" / "rollout-root.jsonl"
    records = records or [session_meta("root"), turn("turn"), atomic("root", "turn", "resp")]
    write_rollout(path, records)
    make_state(settings.codex_home, [thread("root", path, agent_path="/root", role="root")])
    return path


def count_usage(settings):
    with database(settings.database, readonly=True) as conn:
        return conn.execute("SELECT COUNT(*),SUM(total_tokens),SUM(CAST(cost_usd AS REAL)) FROM usage").fetchone()


def test_one_root_session_one_model(dashboard_settings):
    setup_root(dashboard_settings)
    summary = ingest(dashboard_settings)
    assert summary.root_sessions == 1 and summary.usage_records == 1
    assert count_usage(dashboard_settings)[0] == 1


def test_no_preview_removes_previously_stored_preview(dashboard_settings):
    setup_root(dashboard_settings)
    ingest(dashboard_settings)
    private = Settings(
        dashboard_settings.codex_home,
        dashboard_settings.database,
        keep_preview=False,
        running_window_seconds=0,
    )
    ingest(private)
    with database(private.database, readonly=True) as conn:
        assert conn.execute("SELECT first_user_message_preview FROM sessions").fetchone()[0] is None


def test_root_and_subagent_multiple_models(dashboard_settings):
    directory = dashboard_settings.codex_home / "sessions" / "2026" / "09" / "08"
    root_file, child_file = directory / "rollout-root.jsonl", directory / "rollout-child.jsonl"
    source = {"subagent": {"thread_spawn": {"parent_thread_id": "root", "depth": 1, "agent_path": "/root/explorer"}}}
    write_rollout(root_file, [session_meta("root"), turn("rt", "gpt-5.6-sol"), atomic("root", "rt", "root-resp")])
    write_rollout(child_file, [session_meta("child", source), session_meta("root", ordinal=1), turn("ct", "gpt-5.6-luna", 2), atomic("child", "ct", "child-resp", ordinal=3)])
    make_state(dashboard_settings.codex_home, [thread("root", root_file, agent_path="/root"), thread("child", child_file, source=source, model="gpt-5.6-luna", agent_path="/root/explorer")], [("root", "child")])
    summary = ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        agents = conn.execute("SELECT COUNT(*) FROM agents WHERE session_id='root'").fetchone()[0]
        models = {r[0] for r in conn.execute("SELECT DISTINCT model FROM usage")}
    assert sessions == 1 and agents == 2 and summary.subagent_sessions == 1
    assert models == {"gpt-5.6-sol", "gpt-5.6-luna"}


def test_nested_subagents_resolve_to_original_root(dashboard_settings):
    directory = dashboard_settings.codex_home / "sessions" / "2026" / "09" / "08"
    rows, edges = [], []
    for ident, parent, path in (("root", None, "/root"), ("child", "root", "/root/worker"), ("grand", "child", "/root/worker/reviewer")):
        file = directory / f"rollout-{ident}.jsonl"
        source = "cli" if parent is None else {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": path}}}
        write_rollout(file, [session_meta(ident, source), turn(f"t-{ident}"), atomic(ident, f"t-{ident}", f"r-{ident}")])
        rows.append(thread(ident, file, source=source, agent_path=path))
        if parent: edges.append((parent, ident))
    make_state(dashboard_settings.codex_home, rows, edges)
    ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        assert {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM agents")} == {"root"}


def test_repeated_ingestion_is_idempotent(dashboard_settings):
    setup_root(dashboard_settings)
    ingest(dashboard_settings)
    before = tuple(count_usage(dashboard_settings))
    summary = ingest(dashboard_settings, force_all=True)
    after = tuple(count_usage(dashboard_settings))
    assert before == after and summary.usage_records == 0 and summary.duplicate_records == 1


def test_incremental_append(dashboard_settings):
    path = setup_root(dashboard_settings)
    ingest(dashboard_settings)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(atomic("root", "turn", "resp-2", ordinal=4)) + "\n")
    summary = ingest(dashboard_settings)
    assert summary.usage_records == 1 and count_usage(dashboard_settings)[0] == 2


def test_source_file_updated_while_reading_is_deferred(dashboard_settings, monkeypatch):
    path = setup_root(dashboard_settings)
    original_open = Path.open
    appended = False

    def racing_open(self, mode="r", *args, **kwargs):
        nonlocal appended
        if self == path and mode == "rb" and not appended:
            appended = True
            with original_open(self, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(atomic("root", "turn", "late", ordinal=4)) + "\n")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    first = ingest(dashboard_settings)
    assert first.usage_records == 1
    monkeypatch.setattr(Path, "open", original_open)
    second = ingest(dashboard_settings)
    assert second.usage_records == 1 and count_usage(dashboard_settings)[0] == 2


def test_partial_final_line_becomes_complete(dashboard_settings):
    path = dashboard_settings.codex_home / "sessions" / "2026" / "09" / "08" / "rollout-root.jsonl"
    records = [session_meta("root"), turn("turn"), atomic("root", "turn", "resp")]
    write_rollout(path, records, final_newline=False)
    make_state(dashboard_settings.codex_home, [thread("root", path)])
    first = ingest(dashboard_settings)
    assert first.usage_records == 0
    with path.open("a", encoding="utf-8") as handle: handle.write("\n")
    second = ingest(dashboard_settings)
    assert second.usage_records == 1


def test_resumed_session_and_model_switch(dashboard_settings):
    records = [session_meta("root"), turn("one", "gpt-5.6-sol"), atomic("root", "one", "r1"),
               turn("two", "gpt-5.6-terra", 3), atomic("root", "two", "r2", ordinal=4)]
    setup_root(dashboard_settings, records)
    ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT turn_id) FROM usage").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(DISTINCT model) FROM usage").fetchone()[0] == 2


def test_duplicate_source_event_response_id(dashboard_settings):
    duplicate = atomic("root", "turn", "same-response")
    setup_root(dashboard_settings, [session_meta("root"), turn("turn"), duplicate, duplicate])
    summary = ingest(dashboard_settings)
    assert count_usage(dashboard_settings)[0] == 1 and summary.duplicate_records == 1


def test_atomic_and_token_count_are_not_double_counted(dashboard_settings):
    values = usage_values()
    setup_root(dashboard_settings, [session_meta("root"), turn("turn"), atomic("root", "turn", "r", values=values), token_count(values)])
    ingest(dashboard_settings)
    assert count_usage(dashboard_settings)[0] == 1


def test_unknown_price_is_null(dashboard_settings):
    setup_root(dashboard_settings, [session_meta("root"), turn("turn", "future-model"), atomic("root", "turn", "r")])
    summary = ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        row = conn.execute("SELECT cost_usd,price_id FROM usage").fetchone()
    assert row[0] is None and row[1] is None and "openai:future-model" in summary.unknown_prices


def test_orphan_child_is_marked_not_time_joined(dashboard_settings):
    path = dashboard_settings.codex_home / "sessions" / "2026" / "09" / "08" / "rollout-orphan.jsonl"
    source = {"subagent": {"other": "guardian"}}
    write_rollout(path, [session_meta("orphan", source), turn("t"), atomic("orphan", "t", "r")])
    make_state(dashboard_settings.codex_home, [thread("orphan", path, source=source)])
    ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        row = conn.execute("SELECT orphan,parent_thread_id,session_id FROM agents").fetchone()
    assert tuple(row) == (1, None, "orphan")


def test_rollout_parent_metadata_repairs_missing_state_edge(dashboard_settings):
    directory = dashboard_settings.codex_home / "sessions" / "2026" / "09" / "08"
    root_file, guardian_file = directory / "rollout-root.jsonl", directory / "rollout-guardian.jsonl"
    source = {"subagent": {"other": "guardian"}}
    guardian_meta = session_meta("guardian", source)
    guardian_meta["payload"].update({"session_id": "root", "parent_thread_id": "root", "thread_source": "subagent"})
    write_rollout(root_file, [session_meta("root"), turn("rt"), atomic("root", "rt", "rr")])
    write_rollout(guardian_file, [guardian_meta, turn("gt", "codex-auto-review"), atomic("guardian", "gt", "gr")])
    make_state(
        dashboard_settings.codex_home,
        [thread("root", root_file), thread("guardian", guardian_file, source=source, model="codex-auto-review")],
    )
    summary = ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        guardian = conn.execute(
            "SELECT session_id,parent_thread_id,agent_role,orphan FROM agents WHERE thread_id='guardian'"
        ).fetchone()
    assert summary.subagent_sessions == 1 and sessions == 1
    assert tuple(guardian) == ("root", "root", "guardian", 0)
    # A later scan reads no old session_meta lines; the learned edge must still
    # survive state synchronization.
    ingest(dashboard_settings)
    with database(dashboard_settings.database, readonly=True) as conn:
        guardian = conn.execute(
            "SELECT session_id,parent_thread_id,agent_role,orphan FROM agents WHERE thread_id='guardian'"
        ).fetchone()
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions == 1 and tuple(guardian) == ("root", "root", "guardian", 0)


def test_malformed_and_unrecognized_events_do_not_crash(dashboard_settings):
    path = setup_root(dashboard_settings)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
        handle.write(json.dumps({"type": "future_event", "ordinal": 99, "payload": {}}) + "\n")
    summary = ingest(dashboard_settings)
    assert summary.malformed_lines == 1 and count_usage(dashboard_settings)[0] == 1
