# Fleet RLM Terminal UI

This is Fleet's maintained local Node 22.19+ client. It uses
`@earendil-works/pi-tui@0.84.2` to render the backend's AI SDK UI v1 HTTP/SSE
contract; it does not run a model, Harness agent, or Sandbox.

## Run

The simplest local path supervises backend and client together:

```bash
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

Cross-process Session resume requires the configured database. Resume a durable
Session:

```bash
uv run fleet cli -- --session <session-uuid>
pnpm --dir tools/fleet-tui start -- --session <session-uuid>
```

`FLEET_API_URL` changes the default API base URL. Backend runtime settings are
documented in [`../../docs/reference/configuration.md`](../../docs/reference/configuration.md).

## Operator timeline

pi-tui renders one white-and-gray execution timeline in an alternate-screen
viewport. Text, reasoning, code, interpreter output, Tools, Skills, errors,
Results, Artifacts, and usage remain chronological, complete, and expanded
unless the operator folds them. Live and reloaded Turns share one projection
and renderer-neutral store.

The transcript viewport follows the newest output: PgUp/PgDn scroll a page,
Home/End jump to the top/bottom, the mouse wheel scrolls, drag selects text
for copy, and new output re-follows the end. Ctrl+Shift+F opens transcript
search over the scroll view (Enter/Ctrl+G steps to the next match,
Shift+Enter/Ctrl+Shift+G to the previous, Escape closes); matches are styled
from the active Fleet theme. Tool, code, and output cards
fold with Ctrl+O (multi-line tool errors collapse to their summary by
default); the latest card shows a dim key hint.

Assistant, user, reasoning, and Result narrative text is rendered as pi-tui
Markdown, including lists, links, blockquotes, fenced code, and tables. Tool
cards share one panel surface (input/output previews bounded to 4k chars,
long bodies capped with a skip marker). User messages render on an adaptive
surface that keeps contrast against the actual terminal background. The live
activity strip borders a pulse indicator and names the current preparation,
RLM step, Tool, replay, or cancellation action. The footer reports observed
committed input/output tokens, Turn steps, Tools, and outcome; absent provider
telemetry displays as `—` rather than an estimated zero.

Use `/help` for commands. `/theme [name]` lists and switches the builtin
(`dark`/`light`) or custom JSON themes with a filter-as-you-type picker (see below).
`/rename <title>` names the current Session;
`/sessions [title search]` opens the active Session selector. Switching Sessions
keeps the unsent editor text, while a same-Session `/reload` restores pending
Skill/Attachment pins and the `/redo` prompt; those session-scoped pending inputs
clear when switching to another Session. `/skills` and `/skill`
manage up to four exact Skill selections for the next accepted Turn; `/settings`
opens a local TOML policy editor for defaults and named profiles that stays open
for successive field edits, using the freshly saved policy revision each time
(environment-pinned and single-valued fields are read-only). Saving a setting
validates it and requires a Fleet restart to apply. One-shot successes — a saved
setting, a profile selected for restart, an applied theme, updated Skill
selections — surface as transient flash notices above the viewport instead of
permanent transcript messages; failures still land in the transcript. `/cancel` requests
durable Run cancellation. Escape cancels an active Run while preserving the unsent editor
draft. Ctrl+C clears the editor and exits when pressed twice while empty;
Ctrl+D keeps its forward-delete behavior and exits only from an empty editor.

Attachments and Workspace `files/` are first-class inputs/outputs of the
backend Turn contract, so the TUI can move files in both directions:

- `/attach <path>…` uploads local files through `POST /api/attachments` and pins
  them to the next Turn (up to eight); `/attach list` and `/attach clear` manage
  the pins. The RLM receives them as Attachment metadata on the Turn body.
- `/files [path]` lists the Workspace `files/` root via `/api/files` and
  `/file <path>` previews text; `/file <path> save <local>` pages the full
  content and writes it atomically to a local path.
- `/artifact <id> <local>` downloads a committed Artifact with content-length
  and SHA-256 verification and saves it atomically; `/artifacts` lists Artifact
  ids from the current conversation for copy-paste.
- `/redo` resubmits the last prompt with a fresh idempotency key (e.g. after a
  stream interruption); `/reload` re-fetches committed Turns for the current
  Session; `/trace` prints the full MLflow trace ID.

The editor draft, pinned Skills/Attachments, and the last prompt persist per
Session to `~/.local/share/fleet/tui/<session-id>.json` (override with
`FLEET_TUI_STATE_DIR`) and are restored on the next start; a failed write never
blocks the TUI.

## Themes

Builtin `dark` and `light` themes are compiled in; custom themes are JSON files
in `$FLEET_TUI_STATE_DIR/themes/*.json` that may override any token by name and
reference `vars`:

```json
{ "name": "solar", "vars": { "accent": "#ff8800" }, "colors": { "accent": "accent", "border": "#123456" } }
```

Missing tokens fall back to the dark builtin; malformed files are ignored.
`/theme [name]` lists and switches themes (the interactive picker filters as
you type and marks the current theme), the selection persists to
`$FLEET_TUI_STATE_DIR/theme`, `FLEET_TUI_THEME` overrides it at startup, and an
active custom theme hot-reloads when its file changes. Surfaces and selections
are blended adaptively against the terminal's reported background color so
contrast holds on light and dark terminals alike.

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
