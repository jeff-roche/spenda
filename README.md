# Codex Usage Dashboard

A local, server-rendered accounting dashboard for ordinary OpenAI API-key Codex CLI sessions. It reads the state Codex already persists; it does not enable debug tracing, proxy API calls, patch Codex, or change how `codex` is invoked.

## Quick start

```bash
git clone ...
cd codex-usage-dashboard
uv sync

uv run codex-dashboard doctor
uv run codex-dashboard ingest --all
uv run codex-dashboard serve
```

Open <http://127.0.0.1:8765>. The server binds only to loopback by default and refreshes ingestion every 10 seconds.

Overview, session, and model pages include model-colored composition charts for known cost, tokens, and model calls. Session audit rows combine their sequential marker with a safe action category when ordinary rollout metadata provides one, for example `Call #014 · Run tests`; ambiguous calls retain only the marker. Expanding it reveals the full persisted response identity.

The overview's Recent tasks table and the full Tasks / sessions table sort by every displayed data column. Sorting is performed against raw numeric/date values, so abbreviated tokens, percentages, costs, agent counts, and durations retain correct numeric order.

Point an instance at another Codex setup with either `CODEX_HOME=/path` or `--codex-home /path`. `--codex-home` takes precedence. The default dashboard database is:

```text
~/.local/share/codex-usage-dashboard/dashboard.sqlite
```

Override it with `CODEX_DASHBOARD_DB` or `--database`. Codex-owned SQLite and JSONL data are always opened read-only. Only the separate dashboard database is modified.

## Commands

```bash
uv run codex-dashboard doctor
uv run codex-dashboard ingest
uv run codex-dashboard ingest --all
uv run codex-dashboard sessions --sort cost
uv run codex-dashboard prices
uv run codex-dashboard watch --interval 10
uv run codex-dashboard serve --port 8765
uv run codex-dashboard export --format csv
uv run codex-dashboard export --format csv --breakdown models
uv run codex-dashboard export --format json -o sessions.json
uv run codex-dashboard tag SESSION_ID baseline routed-v1
```

Add a historical price without changing application code:

```bash
uv run codex-dashboard price-add MODEL 2026-09-08T00:00:00Z \
  --input 4 --cached-input 0.4 --cache-write 5 --output-price 20 \
  --source https://developers.openai.com/api/docs/models/MODEL
```

Each amount is USD per million tokens. Add a new effective-dated row when a price changes; do not edit the older row. Existing usage for that provider is repriced immediately.

## Data and accounting

The observed Codex data sources, schemas, and version-specific details are in [docs/codex-data-sources.md](docs/codex-data-sources.md). Exact counting, cached-token, reasoning-token, model-attribution, and pricing rules are in [docs/accounting.md](docs/accounting.md).

At a high level, recent Codex versions persist one atomic usage record per API response. Older versions persist a per-response `last_token_usage` beside a cumulative snapshot. The dashboard counts the atomic form where present and never sums cumulative totals. Root/subagent grouping uses explicit spawn edges and structured parent metadata.

The dashboard stores only short task-identification metadata, agent metadata, token counters, prices, and source identities. It does not store assistant responses, tool output, shell output, source code, or full conversations. Use `--no-preview` to disable first-message previews; task titles remain truncated to one line.

## Rebuild safely

```bash
uv run codex-dashboard rebuild
# non-interactive:
uv run codex-dashboard rebuild --yes
```

This clears and reimports only normalized Codex-derived data. Historical prices, aliases, tags, and tag assignments for sessions that still exist are preserved. It refuses to operate if the configured dashboard database is under `CODEX_HOME` and never removes or changes Codex history.

## Known limits

- Costs are local estimates from persisted token records and an effective-dated price table, not OpenAI invoice data.
- Tool-call fees, service-tier adjustments, regional-processing uplifts, and server-side credits are not presently persisted by normal Codex rollouts and are not estimated.
- Any future/legacy subagent that lacks both a parent ID and a root `session_id` is marked as an orphan rather than associated by timing.
- Provider-specific deployments such as Azure remain unpriced unless their own historical price rows are added.
- Unknown models and prices remain visible as `unknown`; they are never silently mapped.
- The first built-in price capture has a documented coverage-floor assumption for pre-capture local history. See the accounting document before treating historical totals as invoice-exact.

ChatGPT Plus quota tracking is intentionally out of scope.
