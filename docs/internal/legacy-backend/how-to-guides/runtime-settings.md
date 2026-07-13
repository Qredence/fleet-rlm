# Runtime Settings

This guide covers the runtime settings surfaces exposed under
`/api/v1/runtime/*`.

## Scope

- `GET /api/v1/runtime/settings` returns the current editable settings snapshot.
- `PATCH /api/v1/runtime/settings` updates local `.env` values only when
  `APP_ENV=local`. In hosted `AUTH_MODE=neon` (BYOK routing), `DAYTONA_*` keys
  are persisted per-workspace as encrypted ciphertext (`FLEET_SECRET_ENCRYPTION_KEY`)
  and masked round-trip values are skipped; non-Daytona keys still return `403`.
  The response reports persisted keys in `updated` and skipped (masked/empty
  round-trip) keys in `skipped`.
- `GET /api/v1/runtime/status` returns current readiness, active models, and
  cached connectivity test results.
- `POST /api/v1/runtime/tests/daytona` and `POST /api/v1/runtime/tests/lm`
  run live connectivity checks.
- `GET/POST/PATCH/DELETE /api/v1/runtime/llm-profiles` manage provider profiles
  (OpenAI, Anthropic, Gemini, OpenAI-compatible proxies).
- `GET/PATCH /api/v1/runtime/llm-roles` read and update per-role profile/model
  bindings for planner, delegate, and delegate_small.
- `POST /api/v1/runtime/llm-profiles/import-env` creates a profile from the
  current `DSPY_*` environment variables in local mode only.

Provider profiles persist in Postgres when `DATABASE_URL` is configured, or in
`.fleet/llm-profiles.json` for SQLite-only local development. In hosted
`AUTH_MODE=neon`, profile reads and writes require repository-admitted identity,
are scoped to the authenticated tenant/user, and do not mutate process
environment or `.env` values. Hosted profile ciphertext uses
`FLEET_SECRET_ENCRYPTION_KEY`; API responses expose only `has_api_key` and a
masked preview.

Gemini uses the OpenAI-compatible endpoint at
`https://generativelanguage.googleapis.com/v1beta/openai/`. Anthropic model lists
are curated statically because Anthropic does not expose a public `/v1/models`
catalog.

## Editable Keys

The runtime settings allowlist is currently:

- `DSPY_LM_MODEL`
- `DSPY_DELEGATE_LM_MODEL`
- `DSPY_DELEGATE_LM_SMALL_MODEL`
- `DSPY_DELEGATE_LM_MAX_TOKENS`
- `DSPY_LLM_API_KEY`
- `DSPY_LM_API_BASE`
- `DSPY_LM_MAX_TOKENS`
- `DAYTONA_API_KEY`
- `DAYTONA_API_URL`
- `DAYTONA_TARGET`

Legacy sandbox-provider and old credential env vars are no longer part of the
editable runtime surface.

## Example Settings Snapshot

```json
{
  "env_path": "/path/to/.env",
  "keys": [
    "DSPY_LM_MODEL",
    "DSPY_DELEGATE_LM_MODEL",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_TARGET"
  ],
  "values": {
    "DSPY_LM_MODEL": "openai/gpt-4o",
    "DSPY_DELEGATE_LM_MODEL": "openai/gpt-4o-mini",
    "DAYTONA_API_KEY": "***",
    "DAYTONA_API_URL": "https://app.daytona.io/api",
    "DAYTONA_TARGET": "default"
  }
}
```

## Example Local Update

```bash
curl -X PATCH http://localhost:8000/api/v1/runtime/settings \
  -H "Content-Type: application/json" \
  -d '{
    "updates": {
      "DSPY_LM_MODEL": "openai/gpt-4o-mini",
      "DAYTONA_API_URL": "https://app.daytona.io/api"
    }
  }'
```

## Runtime Status

`GET /api/v1/runtime/status` reports:

- `ready`
- `sandbox_provider`
- `active_models`
- `llm`
- `daytona`
- `mlflow`
- `tests`
- `guidance`

The runtime provider is Daytona-only on the public surface. Legacy
`SANDBOX_PROVIDER` values in `.env` are ignored during startup rather than
treated as errors.

## Connectivity Tests

### `POST /api/v1/runtime/tests/daytona`

Checks Daytona configuration and API connectivity using the current
`DAYTONA_API_KEY`, `DAYTONA_API_URL`, and optional `DAYTONA_TARGET`.

### `POST /api/v1/runtime/tests/lm`

Checks LM configuration using the current planner model and key settings.

## Daytona Volume Backup and Restore

The durable Daytona volume layout keeps user/session state under
`/home/daytona/memory`. Back up these paths together so memory, skills,
knowledge, and session continuity remain consistent:

| Path | Purpose |
| --- | --- |
| `memories/core.db` | Versioned SQLite memory store. Remote bootstrap stages migrations under `/tmp` before copying the DB back to the volume because Daytona-mounted volumes do not support direct SQLite DDL reliably. |
| `knowledge/index.json` | Versioned knowledge index envelope. |
| `knowledge/ingested/` | Persisted source text for knowledge search. |
| `skills/system/` and `skills/user/` | Bundled and human-curated skills. |
| `sessions/` | Conversation manifests, scratchpads, and workspace links. |
| `buffers/`, `artifacts/`, and `meta/` | Runtime buffers, outputs, and metadata. |

Create backups from a maintenance shell or script running inside the sandbox:

```bash
tar -czf /tmp/fleet-volume-backup.tgz -C /home/daytona/memory \
  memories/core.db \
  knowledge/index.json knowledge/ingested \
  skills/system skills/user \
  sessions buffers artifacts meta
```

Restore by unpacking into the mounted volume, then run:

```bash
uv run python scripts/live_daytona_verify.py
```

Use the live concurrency lane after changing sandbox cleanup or retry behavior:

```bash
FLEET_MAX_CONCURRENT_SANDBOXES=2 uv run python scripts/live_concurrency_verify.py
```

Expected retry/failure behavior:

- Sandbox creation waits for a bounded slot and returns
  `sandbox_concurrency_busy` when the slot timeout is reached.
- Slots are released only after `delete()` or `stop()` succeeds, and duplicate
  release attempts are logged without increasing capacity.
- Failed sandbox creation releases the acquired slot and attempts to delete any
  sandbox created before the failure surfaced.

## Troubleshooting

### Daytona test failing

Check:

- `DAYTONA_API_KEY`
- `DAYTONA_API_URL`
- optional `DAYTONA_TARGET`

The canonical smoke command is:

```bash
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

### LM test failing

Check:

- `DSPY_LM_MODEL`
- `DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY`
- optional `DSPY_LM_API_BASE`

### Settings write rejected

Non-Daytona `PATCH /api/v1/runtime/settings` updates are local-only. Non-local
environments return `403 Forbidden` for those keys. In hosted `AUTH_MODE=neon`,
`DAYTONA_*` keys bypass the `403` and are persisted as encrypted per-workspace
ciphertext; masked round-trip values are skipped (reported in `skipped`, not
`updated`) and empty values do not wipe an existing stored credential.
