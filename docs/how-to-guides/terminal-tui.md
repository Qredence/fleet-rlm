# Use the Fleet terminal UI

`tools/fleet-tui/` is the maintained Node 22.19+ development client for Fleet's
FastAPI HTTP/SSE API. It uses pi-tui 0.84.0 and owns no model, provider key,
Sandbox, or execution runtime.

## Start a supervised session

For Daytona, select an interactive runtime policy profile (the shipped default
is `daytona-recursive`), configure the provider/Daytona values from the [profile
matrix](../reference/profile-matrix.md), and use an upgraded database. The
supervisor verifies Alembic head and never migrates automatically:

```bash
uv run python scripts/db_init.py
uv run fleet cli --port 8000
```

Set the intended interactive profile in `[config] default_profile` in
`config/fleet.toml` first (the shipped default is `daytona-recursive`), or use
`/profiles` and restart Fleet.

See [configuration](../reference/configuration.md) for all settings. Supervised
backend output is stored under `.fleet_rlm/logs/`; `latest.log` identifies the
current launch.

With persistence configured, the client creates a durable Session and prints
its UUID. Resume it later:

```bash
uv run fleet cli -- --session <session-uuid>
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
stay chronological, complete, static, and expanded. Live text, generated code,
and interpreter output are accumulated by stream identity and finalized without
leaving a stale streaming cursor. Daytona forwards ordinary interpreter stdout
as bounded deltas; private `SUBMIT` protocol markers never enter the timeline.
Builtin dark/light palettes follow the terminal preference; `/theme` lists and
switches those builtins or custom JSON themes persisted under
`$FLEET_TUI_STATE_DIR`.

RLM reasoning, generated code, and interpreter output come from Fleet callback
and trajectory Runtime Events, not `dspy.RLM(verbose=...)` or a provider token
stream. Recursive status is bounded backend metadata: Root depth is 0, a native
child is depth 1, and deeper delegation is a Sub-LM fallback without another
Sandbox. The TUI does not infer depth from iteration counts or model text.

The transcript is a follow-end `ScrollView` inside `TuiAltScreen`. PgUp/PgDn
scroll a page, Home/End jump top/bottom, the mouse wheel scrolls, drag selects
text for copy, and new output re-follows the end. Tool, code, and output cards
fold with Ctrl+O. Resize, hydration, and clear may replay the screen and return
to the live bottom.

During a Turn, the activity rail shows phase, safe backend detail, elapsed time,
SSE-derived started/completed step counts, Tool count, and `Ctrl+C`
cancellation. The SSE stream opens immediately with transient `preparation`
status heartbeats while Fleet claims and prepares the Turn; the rail uses those
transient status phases and safe detail from the first byte. Code and
interpreter output remain evidence and never infer step completion.

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
`/sessions`, `/rename`, `/resume`, `/reload`, `/status`, `/settings`,
`/profiles`, `/theme`, `/volume`, `/files`, `/file`, `/attach`, `/artifact`,
`/artifacts`, `/redo`, `/cancel`, `/clear`, `/skills`, `/skill`, `/trace`,
and `/exit`. `/settings`
is a local-only TOML policy editor; `/profiles` switches the selected profile;
`/volume [root]` shows the read-only Workspace Volume tree; `/theme [name]`
lists and switches the builtin or custom color themes. Saved policy
settings take effect after restarting Fleet.

`/attach <path>…` uploads local files through the lifecycle-owned Attachment
endpoint and pins them (up to eight) to the next Turn; `/attach list` and
`/attach clear` manage the pins. `/files [path]` lists the Workspace `files/`
root via `/api/files` and `/file <path>` previews text; `/file <path> save <local>` writes
paged content atomically to a local path. `/artifact <id> <local>` downloads a
committed Artifact with content-length and SHA-256 verification;
`/artifacts` lists Artifact ids in the conversation. `/redo` resubmits the last
prompt with a fresh idempotency key, and `/reload` re-fetches committed Turns
for the current Session without switching Sessions. `/trace` prints the full
MLflow trace ID of the current Run.

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
