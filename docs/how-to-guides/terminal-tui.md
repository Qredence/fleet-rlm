# Use the Fleet terminal UI

`tools/fleet-tui/` is the maintained Node 22.19+ development client for Fleet's
FastAPI HTTP/SSE API. It uses pi-tui 0.82.0 and owns no model, provider key,
Sandbox, or execution runtime.

## Start a supervised session

For Daytona, select the `daytona` runtime policy profile, configure the
LLM/Daytona keys and an upgraded database. The
supervisor verifies Alembic head and never migrates automatically:

```bash
export FLEET_CONFIG_PROFILE=daytona
uv run python scripts/db_init.py
uv run fleet cli --port 8000
```

For the reduced local Deno/Pyodide runtime, select `local-deno`, configure the LLM key and ensure
Deno is on `PATH`. Add a SQLite database URL when Sessions must survive a
backend restart:

```bash
export FLEET_CONFIG_PROFILE=local-deno
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

The screen uses pi coding-agent's built-in visual language. It starts with the
dark palette and switches to the matching light palette when the terminal
reports that preference. User prompts use a distinct message surface, assistant
prose remains unboxed, Markdown and code use semantic highlighting, and Tool
surfaces distinguish pending, successful, and failed execution. Accent,
success, warning, error, muted, and border colors carry meaning rather than
decoration.

Fleet identity and runtime evidence remain unchanged: user/assistant text,
sanitized reasoning, generated code, interpreter output, Tools, Skills,
Attachments, warnings, recoverable errors, Artifacts, typed results, and usage
stay chronological, complete, static, and expanded. The theme is automatic;
there is no theme command or persisted theme setting.

Transcript, activity, editor, and footer still form native terminal history. Fleet
does not capture the mouse, pin the prompt, clip old evidence, or maintain a
transcript viewport. Use the terminal's wheel, trackpad, or
`Shift+PageUp/PageDown` scrollback; plain `PageUp/PageDown` remain editor keys.
Resize, hydration, and clear may replay the screen and return to the live bottom.

During a Turn, the activity rail shows phase, safe backend detail, elapsed time,
SSE-derived started/completed step counts, Tool count, and `Ctrl+C`
cancellation. `PREPARING` means the client is waiting for Fleet's
prepare-before-headers Turn opening; after the stream starts, the rail uses the
backend's transient status phase and safe detail. Code and interpreter output
remain evidence and never infer step completion.

The editor remains writable while a Turn runs, but Enter cannot submit a second
Turn until the active one settles. The footer labels Session token totals and
current/latest Turn step, Tool, replay, and outcome values explicitly. A stream
transport failure after headers is shown as `interrupted`; Fleet never
resubmits the prompt automatically. Use `/resume <current-session-id>` to reload
any history that the backend committed after an interruption.

On resume, the store atomically replaces its state with persisted Turn text and
Fleet trajectory parts. Live and durable data use the same projection rules.
Transient status, delivery, cancellation reason, and latest Run outcome remain
operator state and are not inserted into durable transcript messages.

## Commands and Skills

Use `/help` for the current slash-command list. Important commands include
`/sessions`, `/resume`, `/status`, `/cancel`, `/clear`, `/skills`, `/skill`, and
`/exit`.

`/clear` resets only the current local presentation. It does not delete or
rewrite durable Session History; resuming the Session restores committed Turns.

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
