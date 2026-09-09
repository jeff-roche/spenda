# Coding Agent Usage Dashboard

A local dashboard for token usage, cost, models, projects, and agent activity across Codex, OpenCode, and Claude Code.

The dashboard reads the history these tools already keep on disk. It does not proxy model traffic, enable tracing, or change how any coding agent runs. Source data is opened read-only; normalized accounting is stored in a separate SQLite database.

## Features

- One overview for Codex, OpenCode, and Claude Code, with a source selector for isolated reports.
- Session, model, project, trend, and comparison views.
- Root and subagent grouping where the source records reliable relationships.
- Input, cached input, cache-write, output, reasoning, and total-token accounting.
- Source-reported OpenCode and Claude costs where available, plus effective-dated pricing for Codex.
- CSV and JSON exports with per-source filtering.
- Persistent light and dark themes.
- Deduplicated ingestion, safe incremental cursors where supported, and source-scoped deletion reconciliation.
- A privacy boundary that excludes assistant responses, tool output, source code, and full conversations from the dashboard database.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- At least one supported coding agent with local history

The web interface binds to `127.0.0.1` by default and requires no external service.

## Get started

From this repository:

```bash
uv sync
uv run codex-dashboard doctor
uv run codex-dashboard ingest --all
uv run codex-dashboard serve
```

Open <http://127.0.0.1:8765>. While the server is running, it checks the configured sources for updates every 10 seconds.

## Data sources

| Source | Default location | Override | Accounting behavior |
|---|---|---|---|
| Codex | `${CODEX_HOME:-~/.codex}` | `CODEX_HOME` or `--codex-home` | Reads state SQLite and rollout JSONL files; applies effective-dated prices when a model/provider price is known. |
| OpenCode | `${OPENCODE_DB:-${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db}` | `OPENCODE_DB` or `--opencode-db` | Reads OpenCode's SQLite database and preserves its client-reported costs. |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}` | `CLAUDE_CONFIG_DIR` or `--claude-home` | Reads project transcript JSONL files and uses cumulative `cost-state` values when available. |

Missing sources are skipped. Command-line options take precedence over environment variables.

The dashboard database defaults to:

```text
~/.local/share/codex-usage-dashboard/dashboard.sqlite
```

Set `CODEX_DASHBOARD_DB` or pass `--database` to change it. Validation prevents the dashboard database from being placed inside Codex or Claude source storage or from replacing the OpenCode database.

Accounting rules are documented in [docs/accounting.md](docs/accounting.md).

## Commands

Inspect configured sources and accounting coverage:

```bash
uv run codex-dashboard doctor
```

Ingest once or watch continuously:

```bash
uv run codex-dashboard ingest
uv run codex-dashboard ingest --all
uv run codex-dashboard watch --interval 10
```

List sessions and filter by source:

```bash
uv run codex-dashboard sessions --sort cost
uv run codex-dashboard sessions --source codex
uv run codex-dashboard sessions --source opencode
uv run codex-dashboard sessions --source claude
```

Serve on a different loopback port:

```bash
uv run codex-dashboard serve --port 9000
```

Export normalized accounting:

```bash
uv run codex-dashboard export --format csv --output sessions.csv
uv run codex-dashboard export --format json --source claude --output claude.json
uv run codex-dashboard export --format csv --breakdown models --output models.csv
```

Add a historical model price in USD per million tokens:

```bash
uv run codex-dashboard price-add MODEL 2026-09-08T00:00:00Z \
  --provider openai \
  --input 4 \
  --cached-input 0.4 \
  --cache-write 5 \
  --output-price 20 \
  --source https://example.com/model-pricing
```

Price rows are effective-dated. Add a new row when a price changes so historical calculations remain auditable.

## Privacy and safety

The dashboard stores short task-identification metadata, source paths and identities, agent metadata, token counters, and costs. It does not persist assistant responses, thinking text, tool arguments or output, shell output, attachments, source code, credentials, or full conversations.

Codex first-message previews are enabled by default and truncated to 240 characters. Pass `--no-preview` if you do not want them stored. OpenCode and Claude ingestion do not persist message previews.

Ingestion writes only to the dashboard database. A missing or unreadable source scan does not erase previously normalized Claude data, and reconciliation is scoped to the source being scanned.

## Cost interpretation

Displayed totals include known amounts and silently omit unknown amounts. They should not be treated as invoices.

- Codex costs are calculated from the effective-dated price table. Unknown models or providers remain unpriced.
- OpenCode costs are imported from OpenCode. Free or subscription-backed providers may report zero despite consuming quota.
- Claude costs use persisted `cost-state` snapshots. Older sessions remain unpriced, and root snapshots do not prove coverage of child-agent usage.

Tool fees, service-tier adjustments, regional uplifts, credits, taxes, and other server-side billing changes may be absent from local records.

## Rebuild normalized data

```bash
uv run codex-dashboard rebuild
# Non-interactive:
uv run codex-dashboard rebuild --yes
```

Rebuild clears and reimports normalized sessions, agents, usage, and parser state. It preserves price definitions, aliases, tags, and tag assignments for sessions that still exist. It never removes or changes coding-agent history.

## Troubleshooting

Run `uv run codex-dashboard doctor` first. It prints the resolved source paths, detected session counts, incomplete accounting, models without costs, parser warnings, and dashboard database location.

If a source tab shows zero, select **All time** to rule out the active date filter. Restart the dashboard process after updating the application so new source adapters and interface changes are loaded.

If you run Codex with another home, launch the dashboard with the same value:

```bash
CODEX_HOME=~/.codex_private uv run codex-dashboard serve
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, source-adapter safety rules, and required checks.

## License

Licensed under the [Apache License 2.0](LICENSE).
