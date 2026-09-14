from __future__ import annotations

import pytest

from conftest import atomic, session_meta, token_count, turn, usage_values
from spenda.ingestion.rollout import ParserContext, RolloutParser


def test_atomic_and_duplicate_ui_event():
    parser = RolloutParser()
    parser.parse(session_meta("root"), "source")
    parser.parse(turn("turn"), "source")
    values = usage_values()
    direct = parser.parse(atomic("root", "turn", "resp", values=values), "source")
    duplicate = parser.parse(token_count(values), "source")
    assert direct.usage and direct.usage.response_id == "resp"
    assert duplicate.usage is None and duplicate.ignored_type == "duplicate-token-count"


def test_incremental_last_usage():
    parser = RolloutParser(ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai"))
    result = parser.parse(token_count(usage_values(input_tokens=20)), "source")
    assert result.usage and result.usage.usage.input_tokens == 20


def test_repeated_legacy_snapshot_is_not_counted_twice():
    parser = RolloutParser(ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai"))
    values = usage_values(input_tokens=20, cached=0, write=0, output=2, reasoning=0)
    first = parser.parse(token_count(values, ordinal=1), "source")
    repeated = parser.parse(token_count(values, ordinal=2), "source")
    assert first.usage is not None
    assert repeated.usage is None and repeated.ignored_type == "duplicate-token-count"


def test_cumulative_snapshots_and_reset():
    parser = RolloutParser(ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai"))

    def cumulative(value, ordinal):
        totals = usage_values(input_tokens=value, cached=0, write=0, output=0, reasoning=0)
        return {"timestamp": f"2026-09-08T10:00:0{ordinal}Z", "type": "event_msg", "ordinal": ordinal,
                "payload": {"type": "token_count", "info": {"total_token_usage": totals}}}
    first = parser.parse(cumulative(100, 1), "source")
    second = parser.parse(cumulative(140, 2), "source")
    reset = parser.parse(cumulative(20, 3), "source")
    assert first.usage.usage.input_tokens == 100
    assert second.usage.usage.input_tokens == 40
    assert reset.usage.usage.input_tokens == 20 and reset.warning[0] == "counter_reset"


def test_model_change_and_unknown_model():
    parser = RolloutParser(ParserContext(owner_thread_id="root", provider="openai"))
    parser.parse(turn("a", "gpt-5.6-sol"), "source")
    sol = parser.parse(atomic("root", "a", "one"), "source")
    parser.parse(turn("b", "gpt-5.6-luna", 4), "source")
    luna = parser.parse(atomic("root", "b", "two", ordinal=5), "source")
    fresh = RolloutParser(ParserContext(owner_thread_id="root", provider="openai"))
    unknown = fresh.parse(atomic("root", "z", "three"), "source")
    assert sol.usage.model == "gpt-5.6-sol"
    assert luna.usage.model == "gpt-5.6-luna"
    assert unknown.usage.model == "unknown-model"


def test_subagent_file_owner_not_overwritten_by_copied_parent_meta():
    parser = RolloutParser()
    parser.parse(session_meta("child", {"subagent": {"thread_spawn": {"parent_thread_id": "root"}}}), "source")
    parser.parse(session_meta("root", "cli", ordinal=1), "source")
    assert parser.context.owner_thread_id == "child"


def test_unrecognized_event_is_ignored():
    result = RolloutParser().parse({"type": "future_event", "payload": {"schema": 99}}, "source")
    assert result.usage is None and result.ignored_type == "future_event"


@pytest.mark.parametrize("invalid", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_json_token_values_are_normalized(invalid):
    parser = RolloutParser(
        ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai")
    )
    values = usage_values(input_tokens=invalid, cached=invalid, output=invalid, reasoning=invalid)

    result = parser.parse(atomic("root", "t", "response", values=values), "source")

    assert result.usage is not None
    assert result.usage.usage.fingerprint() == (0, 0, 400, 0, 0, 0)


def test_safe_call_labels_from_persisted_response_metadata():
    parser = RolloutParser(ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai"))

    parser.parse(
        {"type": "response_item", "ordinal": 1, "payload": {
            "type": "custom_tool_call", "name": "exec",
            "input": 'const r = await tools.exec_command({cmd:"uv run pytest"});',
        }},
        "source",
    )
    tests = parser.parse(atomic("root", "t", "tests", ordinal=2), "source")
    assert tests.usage and tests.usage.call_label == "Run tests"

    parser.parse(
        {"type": "event_msg", "ordinal": 3, "payload": {
            "type": "item_completed", "item": {"type": "FileChange"},
        }},
        "source",
    )
    patch = parser.parse(atomic("root", "t", "patch", ordinal=4), "source")
    assert patch.usage and patch.usage.call_label == "Apply file change"

    parser.parse(
        {"type": "response_item", "ordinal": 5, "payload": {
            "type": "custom_tool_call", "name": "view_image", "input": "{}",
        }},
        "source",
    )
    image = parser.parse(atomic("root", "t", "image", ordinal=6), "source")
    assert image.usage and image.usage.call_label == "Inspect image"

    parser.parse(
        {"type": "response_item", "ordinal": 7, "payload": {
            "type": "message", "role": "assistant", "phase": "final_answer", "content": [],
        }},
        "source",
    )
    final = parser.parse(atomic("root", "t", "final", ordinal=8), "source")
    assert final.usage and final.usage.call_label == "Final response"


def test_ambiguous_call_keeps_no_label_and_completed_action_does_not_leak():
    parser = RolloutParser(ParserContext(owner_thread_id="root", turn_id="t", model="gpt-5.6-sol", provider="openai"))
    values = usage_values()
    first = parser.parse(atomic("root", "t", "one", values=values), "source")
    assert first.usage and first.usage.call_label is None

    parser.parse(
        {"type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "CommandExecution", "command": "make"},
        }},
        "source",
    )
    parser.parse(token_count(values), "source")
    second = parser.parse(atomic("root", "t", "two", ordinal=4), "source")
    assert second.usage and second.usage.call_label is None
