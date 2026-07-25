# Fleet RLM Terminal UI

This is Fleet's maintained local Node 22.19+ client. It uses
`@earendil-works/pi-tui@0.82.0` to render the backend's AI SDK UI v1 HTTP/SSE
contract; it does not run a model, Harness agent, or Sandbox.

## Run

The simplest local path supervises backend and client together:

```bash
FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3' \
  uv run fleet deno --port 8000 # LLM key + Deno required
uv run fleet cli --port 8000    # LLM key + Daytona key + database required
```

`fleet cli` verifies that the configured database is at Alembic head. Initialize
or upgrade it explicitly with `uv run python scripts/db_init.py`; the supervisor
never migrates automatically. `uv run fleet doctor daytona` performs an opt-in
disposable provider/mount/interpreter probe.

Run separately against an existing API:

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
pnpm --dir tools/fleet-tui start -- --api-url http://127.0.0.1:8000
```

The database is optional for an ephemeral Deno process, but cross-process
Session resume requires it. Resume a durable Session:

```bash
uv run fleet cli -- --session <session-uuid>
pnpm --dir tools/fleet-tui start -- --session <session-uuid>
```

`FLEET_API_URL` changes the default API base URL. Backend runtime settings are
documented in [`../../docs/reference/configuration.md`](../../docs/reference/configuration.md).

## Operator timeline

pi-tui renders one white-and-gray execution timeline. Text, reasoning, code,
interpreter output, Tools, Skills, errors, Results, Artifacts, and usage remain
chronological, complete, static, and expanded. Live and reloaded Turns share one
projection and renderer-neutral store.

Fleet does not capture the mouse, clip old messages, or maintain a transcript
viewport. Use native terminal scrollback. Assistant, user, reasoning, and Result
narrative text is rendered as pi-tui Markdown, including lists, links,
blockquotes, fenced code, and tables. The live activity loader names the current
preparation, RLM step, Tool, replay, or cancellation action. The footer reports
observed committed input/output tokens, Turn steps, Tools, and outcome; absent
provider telemetry displays as `—` rather than an estimated zero.

Use `/help` for commands. `/rename <title>` names the current Session;
`/sessions [title search]` opens the active Session selector. Switching Sessions
preserves the unsent draft and pending Skill selections. `/skills` and `/skill`
manage up to four exact Skill selections for the next accepted Turn; `/settings`
opens local TOML policy selectors for defaults and named profiles. Saving a
setting validates it and requires a Fleet restart to apply. `/cancel` requests
durable Run cancellation. Escape cancels an active Run while preserving the unsent editor
draft. Ctrl+C clears the editor and exits when pressed twice while empty;
Ctrl+D keeps its forward-delete behavior and exits only from an empty editor.

## Artifact download

```bash
pnpm --dir tools/fleet-tui start -- \
  artifact <artifact-uuid> --output ./result.bin
```

Add `--api-url <url>` when needed. The command validates content length and
SHA-256, fsyncs a temporary file, and atomically renames it. An integrity failure
removes the partial file.

## Validate

```bash
pnpm --dir tools/fleet-tui run format:check
pnpm --dir tools/fleet-tui run lint
pnpm --dir tools/fleet-tui run typecheck
pnpm --dir tools/fleet-tui run test
```

From the repository root, `make check` runs these checks plus backend, generated
contract, codebase, and documentation lanes.
