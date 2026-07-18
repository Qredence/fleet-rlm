# Use the Fleet terminal UI

`tools/fleet-tui/` is the maintained Node 22.19+ development client for Fleet's
FastAPI HTTP/SSE API. It uses pi-tui 0.80.10 and owns no model, provider key,
Sandbox, or execution runtime.

## Start a supervised session

For Daytona, configure the LLM/Daytona keys and an upgraded database. The
supervisor verifies Alembic head and never migrates automatically:

```bash
uv run python scripts/db_init.py
uv run fleet cli --port 8000
```

For the reduced local Deno/Pyodide runtime, configure the LLM key and ensure
Deno is on `PATH`. Add a SQLite database URL when Sessions must survive a
backend restart:

```bash
export FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3'
uv run fleet deno --port 8000
```

See [configuration](../reference/configuration.md) for all settings. Supervised
backend output is stored under `.fleet_rlm/logs/`; `latest.log` identifies the
current launch.

With persistence configured, the client creates a durable Session and prints
its UUID. Resume it later:

```bash
uv run fleet cli -- --session <session-uuid>
uv run fleet deno -- --session <session-uuid>
```

To run the backend and client separately:

```bash
uv run fleet web --port 8000
pnpm --dir tools/fleet-tui start -- --api-url http://127.0.0.1:8000
```

## Timeline and interaction

The screen is a flat achromatic operator timeline. User/assistant text,
sanitized reasoning, generated code, interpreter output, Tools, Skills,
Attachments, warnings, recoverable errors, Artifacts, typed results, and usage
remain chronological, complete, static, and expanded.

Transcript, activity, editor, and footer form native terminal history. Fleet
does not capture the mouse, pin the prompt, clip old evidence, or maintain a
transcript viewport. Use the terminal's wheel, trackpad, or
`Shift+PageUp/PageDown` scrollback; plain `PageUp/PageDown` remain editor keys.
Resize, hydration, and clear may replay the screen and return to the live bottom.

During a Turn, the activity rail shows phase, safe backend detail, elapsed time,
step/tool counts, and `Ctrl+C` cancellation. The footer shows the current model
when reported, token usage, completed steps, and tool count. The backend
currently may leave the model display as `—` when the stream does not identify
it.

On resume, the store atomically replaces its state with persisted Turn text and
Fleet trajectory parts. Live and durable data use the same projection rules.

## Commands and Skills

Use `/help` for the current slash-command list. Important commands include
`/sessions`, `/resume`, `/status`, `/cancel`, `/clear`, `/skills`, `/skill`, and
`/exit`.

`/skills` lists discoverable Skill Cards. `/skill <name-or-id>` pins the current
discoverable version for the next accepted Turn;
`/skill <hidden-uuid>@<version>` pins an explicit-only Skill without exposing it
in discovery. `/skill clear` clears pending selections. At most four unique
Skills may be pending.

Selections clear after the Turn stream opens. A pre-header validation or network
failure retains them for correction/retry. The server progressively loads full
Skill instructions/resources and performs every Tool or interpreter call.

## Artifact download

Download a committed Artifact without starting the interactive UI:

```bash
pnpm --dir tools/fleet-tui start -- \
  --api-url http://127.0.0.1:8000 \
  artifact <artifact-uuid> --output ./result.bin
```

The client writes a unique temporary file, verifies the response content length
and SHA-256 digest from the integrity headers, fsyncs it, and atomically renames
it to the requested output. A mismatch removes the temporary file and does not
replace the destination.

## Daytona diagnostics

Run `uv run fleet doctor daytona` before diagnosing provider failures. This
opt-in check validates settings, database head, provider access, scoped Volume
mounting, and interpreter execution through one labelled disposable Sandbox. It
creates no Fleet domain rows and reports only sanitized categories/actions.
