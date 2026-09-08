from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


SESSION_AGGREGATE = """
WITH agent_agg AS (
  SELECT session_id,COUNT(*) agent_count FROM agents GROUP BY session_id
), usage_agg AS (
  SELECT session_id,GROUP_CONCAT(DISTINCT model) models_used,
         SUM(input_tokens) input_tokens,SUM(cached_input_tokens) cached_input_tokens,
         SUM(cache_write_input_tokens) cache_write_input_tokens,
         SUM(uncached_input_tokens) uncached_input_tokens,SUM(output_tokens) output_tokens,
         SUM(reasoning_output_tokens) reasoning_tokens,SUM(total_tokens) total_tokens,
         SUM(CAST(cost_usd AS REAL)) known_cost_usd,
         SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) unknown_cost_records,
         COUNT(*) usage_events
  FROM usage GROUP BY session_id
), tag_agg AS (
  SELECT st.session_id,GROUP_CONCAT(t.name) tags
  FROM session_tags st JOIN tags t ON t.id=st.tag_id GROUP BY st.session_id
)
SELECT s.*,
       COALESCE(a.agent_count,0) AS agent_count,
       u.models_used,
       COALESCE(u.input_tokens,0) AS input_tokens,
       COALESCE(u.cached_input_tokens,0) AS cached_input_tokens,
       COALESCE(u.cache_write_input_tokens,0) AS cache_write_input_tokens,
       COALESCE(u.uncached_input_tokens,0) AS uncached_input_tokens,
       COALESCE(u.output_tokens,0) AS output_tokens,
       COALESCE(u.reasoning_tokens,0) AS reasoning_tokens,
       COALESCE(u.total_tokens,0) AS total_tokens,
       u.known_cost_usd,
       COALESCE(u.unknown_cost_records,0) + CASE WHEN s.accounting_status!='complete' THEN 1 ELSE 0 END AS unknown_cost_records,
       COALESCE(u.usage_events,0) AS usage_events,
       CAST(strftime('%s',s.updated_at)-strftime('%s',s.created_at) AS INTEGER) AS duration_seconds,
       t.tags
FROM sessions s
LEFT JOIN agent_agg a ON a.session_id=s.id
LEFT JOIN usage_agg u ON u.session_id=s.id
LEFT JOIN tag_agg t ON t.session_id=s.id
"""


def session_rows(
    conn: sqlite3.Connection,
    *,
    where: str = "1=1",
    params: tuple[Any, ...] = (),
    order: str = "started",
    direction: str = "desc",
    limit: int | None = None,
) -> list[sqlite3.Row]:
    allowed_orders = {
        "time": "julianday(s.created_at)",
        "started": "julianday(s.created_at)",
        "title": "LOWER(COALESCE(s.title,''))",
        "project": "LOWER(COALESCE(s.repo_name,s.cwd,''))",
        "root_model": "LOWER(COALESCE(s.root_model,''))",
        "models": "LOWER(COALESCE(models_used,''))",
        "agents": "agent_count",
        "input": "input_tokens",
        "cached": "CASE WHEN input_tokens>0 THEN 1.0*cached_input_tokens/input_tokens ELSE 0 END",
        "output": "output_tokens",
        "tokens": "total_tokens",
        "total": "total_tokens",
        "cost": "COALESCE(known_cost_usd,0.0)",
        "duration": "duration_seconds",
    }
    expression = allowed_orders.get(order, allowed_orders["started"])
    order_direction = "ASC" if direction.lower() == "asc" else "DESC"
    sql = SESSION_AGGREGATE + f" WHERE {where} ORDER BY {expression} {order_direction}, s.created_at DESC, s.id"
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    return conn.execute(sql, params).fetchall()


def session_detail(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    rows = session_rows(conn, where="s.id=?", params=(session_id,))
    return rows[0] if rows else None


def format_tokens(value: int | None) -> str:
    number = int(value or 0)
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(number) >= divisor:
            number_text = f"{number / divisor:.2f}".rstrip("0").rstrip(".")
            return f"{number_text}{suffix}"
    return f"{number:,}"


def format_cost(value: float | str | None, unknown: int = 0) -> str:
    amount = float(value or 0)
    digits = 4 if abs(amount) < 10 else 2
    return f"${amount:,.{digits}f}"


def format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def iso_date(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def as_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["cost_usd"] = None if result.get("unknown_cost_records") else result.get("known_cost_usd")
    return result
