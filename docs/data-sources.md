# Data sources

The dashboard reads local coding-agent history without changing the source
applications. Each adapter opens source files or databases read-only and writes
only normalized metadata, token counters, and costs to the dashboard database.
It never stores prompts, assistant text, thinking text, tool arguments or
output, attachments, source code, credentials, or full conversations.

## Codex

Codex is discovered from `${CODEX_HOME:-~/.codex}` or `--codex-home`. The
adapter reads the latest `state_*.sqlite` database and rollout JSONL files under
`sessions/` and `archived_sessions/`.

The state database supplies thread metadata, project and Git fields, agent
attributes, and explicit parent-child relationships from `thread_spawn_edges`.
Rollouts supply the auditable per-response accounting records. The preferred
record is `token_usage_record.payload.usage`, identified by `response_id`.
Older rollouts use `event_msg` / `token_count` records and compute deltas from
the categorized cumulative counter. Thread-level aggregate counters are used
only for diagnostics and are never imported as usage rows.

`turn_context` supplies the model and reasoning effort active for subsequent
responses. Input includes cached-input and cache-write subsets; reasoning is a
subset of output. Partial final JSONL lines remain unread until a later scan.
Stable response identities and dashboard-owned byte offsets make rescans,
rollout moves, and process restarts safe.

## OpenCode

OpenCode is discovered from `OPENCODE_DB`, or from
`${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db`, and accepts
`--opencode-db`. The adapter opens the SQLite database with `mode=ro` and
`PRAGMA query_only=ON`.

It reads session and project metadata from `session` and `project`, plus scalar
assistant accounting fields extracted from `message.data`. When available, it
also extracts only the part type and tool name from `part.data` to produce a
fixed action label; it never selects part content, tool arguments, or output,
and never reads the `credential` table. Top-level sessions become dashboard
tasks; sessions with `parent_id` become nested agents. Imported identifiers
are prefixed with `opencode:`.

Each assistant message is one usage row, keyed by its stable message ID.
`tokens.input`, cache reads, cache writes, output, and reasoning are normalized
so cached and cache-write values remain input subsets, and reasoning remains an
output subset. OpenCode's recorded `cost` is preserved directly and is never
repriced from the Codex price table. A dashboard-owned update cursor supports
incremental scans, while a complete read reconciles only OpenCode-derived rows.

## Claude Code

Claude Code is discovered from `CLAUDE_CONFIG_DIR`, or from `~/.claude`, and
accepts `--claude-home`. Root transcripts are read from:

```text
<claude-home>/projects/<project>/<session-id>.jsonl
```

Subagent transcripts are read from:

```text
<claude-home>/projects/<project>/<root-session-id>/subagents/agent-<agent-id>.jsonl
```

Root transcripts become dashboard tasks and subagent transcripts become agents
under their root. Imported identifiers are prefixed with `claude:`. The adapter
uses transcript envelope metadata and scalar assistant fields. It also inspects
only assistant content-block types and tool names to produce a fixed action
label; content bodies and tool arguments are discarded.

Assistant usage is keyed by `(sessionId, message.id)`. Streaming updates for
the same message keep the latest record. Uncached input, cache-read input,
cache-creation input, output, and thinking-token subsets map directly into the
normalized token fields. Claude's output already includes thinking tokens.

`cost-state` records are cumulative. Consecutive snapshots become dated cost
changes whose sum equals the latest source total, preserving historical trends
without inventing per-message dollar allocations. A root cost state does not
prove coverage of subagent usage, so those sessions remain partial.

Claude scans import valid records from a readable transcript even when another
line or transcript has an error. Destructive reconciliation is limited to
complete, readable scope: a fully read transcript can reconcile its own rows,
and removal of missing transcripts requires a complete directory scan.

## Accounting and source changes

See [accounting.md](accounting.md) for normalization and pricing formulas.
Source formats are implementation details that can change with each tool
release. An incompatible OpenCode schema is reported as a source error; missing
or unreadable Claude data preserves previously imported rows; unknown Codex
rollout records are ignored rather than interpreted as usage.
