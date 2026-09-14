"""Privacy-safe labels derived only from action types and tool names."""

from __future__ import annotations

from typing import Any

_PRIORITY = {
    "Assistant response": 10,
    "Update task plan": 30,
    "Ask user": 40,
    "Read files": 50,
    "Search files": 50,
    "Inspect image": 55,
    "Run command": 60,
    "Fetch webpage": 60,
    "Search the web": 60,
    "Use external tool": 60,
    "Use tool": 60,
    "Start subagent": 70,
    "Stop subagent": 70,
    "Apply file change": 80,
}


def _normalized(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def safe_action_label(part_type: Any, tool_name: Any = None) -> str | None:
    """Return a fixed label without inspecting action arguments or content."""

    kind = _normalized(part_type)
    tool = _normalized(tool_name)
    if kind in {"text", "message"} and not tool:
        return "Assistant response"
    if kind in {"patch", "file_change"}:
        return "Apply file change"
    if not tool:
        return None

    if tool in {"edit", "write", "patch", "apply_patch", "multiedit"}:
        return "Apply file change"
    if tool in {"read", "read_file"}:
        return "Read files"
    if tool in {"grep", "glob", "search", "search_files", "find"}:
        return "Search files"
    if tool in {"bash", "shell", "exec", "exec_command", "local_shell_call"}:
        return "Run command"
    if tool in {"view_image", "image_view"}:
        return "Inspect image"
    if tool in {"websearch", "web_search", "search_query"}:
        return "Search the web"
    if tool in {"webfetch", "web_fetch"}:
        return "Fetch webpage"
    if tool in {"agent", "task", "spawn_agent", "create_agent"}:
        return "Start subagent"
    if tool in {"taskstop", "task_stop", "stop_agent"}:
        return "Stop subagent"
    if tool in {"askuserquestion", "ask_user_question", "question"}:
        return "Ask user"
    if tool in {"todowrite", "todo_write", "update_plan"}:
        return "Update task plan"
    if tool.startswith("mcp__"):
        return "Use external tool"
    return "Use tool"


def prefer_action_label(current: str | None, candidate: str | None) -> str | None:
    """Keep the most descriptive label when one response contains several parts."""

    if candidate is None:
        return current
    if current is None or _PRIORITY.get(candidate, 0) > _PRIORITY.get(current, 0):
        return candidate
    return current
