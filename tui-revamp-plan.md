# Fleet TUI Revamp Plan

## Overview

Upgrade `@earendil-works/pi-tui` from `0.87.1` to `1.0.3` and revamp the Fleet
TUI in a single integrated effort. The two concerns share the same file surface,
so splitting them creates double churn.

**Scope:** `tools/fleet-tui/` exclusively. No backend changes.  
**Approach:** Minimal, targeted changes — upgrade the package, fix all
import/API breakage, then layer the UX revamp on top of the now-compiling base.

**What is not changing:**
- Backend contract, SSE event types, generated files (`src/generated/`)
- Stream lifecycle, adapters, reducers, core state machine
- CLI entry point logic
- Single-panel fullscreen layout (no persistent sidebar added)

---

## Sub-Task 1 — Audit the pi-tui 1.0.3 API surface

**Status:** `[ ] pending`

**Intent**

Identify every breaking or changed API between `0.87.1` and `1.0.3` that affects
imports in `tools/fleet-tui/src/`. Produce a concrete migration diff table that
Sub-Task 2 executes against.

**Expected Outcomes**

- A verified list of removed, renamed, or changed exports used by Fleet TUI.
- Confirmed list of new APIs to adopt (theme, keybindings, color helpers).
- No remaining ambiguity about what needs changing before bumping the version.

**Todo List**

1. Install `@earendil-works/pi-tui@1.0.3` in a scratch context and extract its
   `index.d.ts` exports.
2. Diff against the 0.87.1 exports to identify removed/renamed symbols.
3. Cross-reference every `from "@earendil-works/pi-tui"` import in these files:
   - `application.ts` — `Editor`, `ProcessTerminal`, `TuiAltScreen`, `Terminal`
   - `screen.ts` — `Box`, `Loader`, `ScrollView`, `Text`, `truncateToWidth`,
     `VStack`, `Component`, `Editor`, `Terminal`, `TUI`
   - `theme.ts` — all theme helpers
   - `keybindings.ts` — `KeybindingsManager`
   - `message-renderer.ts`, `terminal-text.ts`, `transcript.ts`
   - `autocomplete.ts`, `command-presenter.ts`, `presenter/overlay.ts`,
     `presenter/settings.ts`, `presenter/skill-selector.ts`
   - All test files in `tui/tests/`
4. Document which calls break and what the 1.0.3 replacement is.
5. Note the new `theme.style()` / `theme.colors` / `theme.appearance` API shape.
6. Note the `scrollbarTrack` / `scrollbarThumb` `ScrollView` options (added in
   0.84.4 changelog — already used in Fleet; confirm still present in 1.0.3).

**Relevant Context**

- All pi-tui imports: `tools/fleet-tui/src/` (19 import sites identified).
- 0.87.0 breaking changes: `shouldStopAfterTurn` removed, `ContextEditEntry`
  added to union, `SessionManager` canonical. These are SDK-level changes that
  don't affect Fleet TUI (it doesn't use the agent SDK, only pi-tui components).
- 0.99.0 TUI-relevant: new `system` theme, `#rgb`/`oklch()`/`okhsl()` colors,
  `theme.style()`, `theme.colors`, `theme.appearance`. Build switch from native
  TS preview to TS 7.0; replaced `tsx` with Node type stripping — Fleet TUI uses
  `tsx` in its own scripts; check if that changes.
- 1.0.0: fullscreen by default (Fleet already uses `TuiAltScreen`, so aligned).
- 1.0.3: `Home`/`End` keybinding semantics changed (editor cursor only;
  transcript top/bottom → `Ctrl+Home`/`Ctrl+End`).

---

## Sub-Task 2 — Bump package version and fix all compilation breakage

**Status:** `[ ] pending`

**Intent**

Update `package.json` to `@earendil-works/pi-tui@1.0.3`, then fix every
import error, type error, and renamed-symbol reference surfaced by Sub-Task 1.
After this sub-task, `make tui-check` passes with the new version.

**Expected Outcomes**

- `package.json` pinned to `1.0.3`.
- Zero TypeScript compile errors (`tsc --noEmit`).
- All existing tests pass (`vitest run`).
- Lint and format clean (`biome lint`, `biome ci`).

**Todo List**

1. Edit `tools/fleet-tui/package.json`: set `@earendil-works/pi-tui` to `1.0.3`.
2. Run `pnpm install` from `tools/fleet-tui/`.
3. Run `pnpm typecheck` and collect all errors.
4. Apply the migration diff from Sub-Task 1 to fix each broken import/call site.
5. Pay particular attention to `theme.ts`: if `setTerminalColorScheme`,
   `queryTerminalColorScheme`, or `queryTerminalBackgroundColor` signatures
   changed, fix callers in `application.ts`.
6. Run `make tui-check` to confirm clean state before proceeding.

**Relevant Context**

- Run pnpm commands from `tools/fleet-tui/`.
- Run Make targets from repo root.
- `make tui-check` = generated API/stream checks + format + lint + typecheck + tests.
- Do not hand-edit `src/generated/`; regenerate via `make api-sync` /
  `make stream-sync` if the openapi contract changed (it has not — backend unchanged).

---

## Sub-Task 3 — Migrate theme layer to 1.0.3 APIs

**Status:** `[ ] pending`

**Intent**

Replace all deprecated/old theme call patterns with the new `theme.style()`,
`theme.colors`, and `theme.appearance` APIs. Adopt `system` as the default
theme. This is the foundation all subsequent UI work depends on.

**Expected Outcomes**

- `theme.ts` uses `theme.style()` for all styled text, not ad-hoc ANSI strings.
- `theme.colors` used for concrete color access where needed.
- `theme.appearance` (`"dark"` | `"light"`) used for contrast decisions.
- Default theme is `system` (terminal palette auto-detection).
- `FLEET_TUI_THEME` env var still respected for explicit overrides.
- Existing `dark`/`light`/`forest`/`ocean` named themes still selectable via
  `/theme`.
- No visual regression in existing message rendering — all prior theme tokens
  (`accent`, `muted`, `dim`, `success`, `warning`, `error`, `toolPanelBg`,
  `userMessageBg`, `searchMatch`) still map to correct semantic colors.

**Todo List**

1. Read the full `theme.ts` and identify every use of `theme.fg()`, `theme.bg()`,
   `theme.surface()`, `theme.bold()`, `theme.searchMatch()`, etc.
2. Audit whether `theme.fg()` / `theme.bg()` still exist in 1.0.3 (changelog
   says they "remain available"). Keep them where they still work; migrate
   compound styled calls to `theme.style()`.
3. Update `initTheme()` to default to `"system"` when no override is set.
4. Ensure `onThemeChange()` / `stopThemeMonitoring()` still exist and are called
   correctly in `application.ts`.
5. Update all callers in `screen.ts`, `message-renderer.ts`, `transcript.ts`,
   `command-presenter.ts`, `presenter/` files.
6. Run `make tui-check`.

**Relevant Context**

- `theme.ts`: `tools/fleet-tui/src/tui/theme.ts`
- All 19 pi-tui import sites touch theme directly or indirectly.
- The 0.99.0 changelog says: "Added `#rgb`, `oklch()`, and `okhsl()` colors and
  an optional `appearance` field to theme files, and `theme.style()`,
  `theme.colors`, and `theme.appearance` for extensions." These are the new APIs.
- The `system` theme "derives pi's colors from the terminal's reported foreground,
  background, and ANSI palette and rebuilds them when the terminal switches
  between light and dark."

---

## Sub-Task 4 — Update keybindings to 1.0.3 defaults

**Status:** `[ ] pending`

**Intent**

Align `keybindings.ts` and the footer hints with 1.0.3's changed `Home`/`End`
semantics. Update every place that documents or depends on these bindings.

**Expected Outcomes**

- `Home`/`End` move the editor cursor to line start/end (not transcript scroll).
- `Ctrl+Home`/`Ctrl+End` scroll the transcript to top/bottom.
- Footer keybinding hints reflect the new scheme.
- `keybindings.ts` binding table updated.
- All tests that assert on keybinding behavior updated.

**Todo List**

1. Read `keybindings.ts` in full.
2. Locate any `Home`/`End` bindings in the Fleet keybinding table and the
   `TuiAltScreen` scroll configuration.
3. Update to: editor `Home`/`End` = line start/end (likely handled by pi-tui
   automatically now); transcript scroll top/bottom = `Ctrl+Home`/`Ctrl+End`.
4. Read `screen.ts` footer rendering and update hint text.
5. Check `tui/tests/viewport-scroll.test.ts` for assertions that depend on old
   `Home`/`End` scroll behavior; update.
6. Run `make tui-check`.

**Relevant Context**

- `keybindings.ts`: `tools/fleet-tui/src/tui/keybindings.ts`
- Footer rendering: `FooterComponent` in `screen.ts`
- 1.0.3 changelog: "`Home`/`End` now always move the editor cursor to the line
  start/end; fullscreen transcript top/bottom moved to `Ctrl+Home`/`Ctrl+End`,
  which no longer move the editor cursor."

---

## Sub-Task 5 — Add header bar (session title, model, status)

**Status:** `[ ] pending`

**Intent**

Add a static one-line header bar at the top of the `FleetScreen` layout that
shows the session title, current model name, and session status. This gives
the operator immediate context without needing to run `/status`.

**Expected Outcomes**

- A `HeaderComponent` renders one line above the transcript `ScrollView`.
- Displays: session title (truncated to fit), model name from settings, session
  status (`active` / `archived`).
- Updates reactively when session state changes (session rename, status change).
- Uses `theme.style()` with semantic tokens: title in `accent`, model in `muted`,
  status badge styled by value.
- Does not add interactive elements (static display only).
- No layout shift: header shrinks from the transcript area only, not from
  editor or footer.

**Todo List**

1. Create `tools/fleet-tui/src/tui/header.ts` with `HeaderComponent`.
2. `HeaderComponent` reads from `ConversationStore.getState().session` and
   `getState().settings` (add `settings` to store if not present — see
   Sub-Task 7 for full settings integration; use a minimal stub here).
3. Add model name to store state: read it from the `SettingsPolicyResponse`
   already loaded at startup via `GET /api/settings`.
4. Add `HeaderComponent` as the first child of `FleetScreen` in `screen.ts`
   with `shrink: 0`.
5. Ensure `FleetScreen.invalidate()` propagates to the header.
6. Write a unit test in `tui/tests/` covering header render for
   active/archived/streaming states.
7. Run `make tui-check`.

**Relevant Context**

- `FleetScreen` layout: `tools/fleet-tui/src/tui/screen.ts` lines 37–63.
- Session state: `tools/fleet-tui/src/tui/store.ts` — `session` field.
- Settings API: `GET /api/settings` → `SettingsPolicyResponse`; already called
  by `/settings` command via `client.getSettings()`.
- Model name lives at `settings.fields.llm.root.model` (from `fleet.toml`
  analysis: `[llm.root] model = "..."`).

---

## Sub-Task 6 — Revamp footer with richer status and keybinding hints

**Status:** `[ ] pending`

**Intent**

Replace the current minimal footer with a two-zone footer: left side shows
live run status (phase, step count, elapsed time), right side shows contextual
keybinding hints that change based on current `Phase`. This makes the interface
self-documenting without cluttering the transcript.

**Expected Outcomes**

- `FooterComponent` in `screen.ts` has two zones: left=status, right=hints.
- Hints are contextual: idle shows `Ctrl+C exit · / commands · Ctrl+Z suspend`;
  streaming shows `Ctrl+C cancel`; cancelling shows `cancelling…`.
- Live run phase label replaces the current status glyph when a run is active.
- Elapsed time shown during streaming (`⏱ 12s`).
- Uses `theme.style()` throughout.
- Footer stays one line (no wrap).

**Todo List**

1. Read the current `FooterComponent` implementation in `screen.ts` in full.
2. Define a `footerHints(phase: Phase): string` helper that returns the right
   zone string for each phase: `idle`, `submitting`, `running`, `cancelling`,
   `completed`, `error`.
3. Left zone: show run phase label + elapsed seconds when busy; show session
   turn count when idle.
4. Right zone: contextual keybinding hints from the helper.
5. Truncate with `truncateToWidth` so the footer never wraps.
6. Update `dispose()` path in `OperatorDockComponent` if needed.
7. Run `make tui-check`.

**Relevant Context**

- Current footer: `FooterComponent` in `tools/fleet-tui/src/tui/screen.ts`.
- Phase type: `tools/fleet-tui/src/tui/store.ts`.
- `keyHint` helper: `tools/fleet-tui/src/tui/keybinding-hints.ts`.
- Elapsed time: available from run state in store (start timestamp vs. now).

---

## Sub-Task 7 — Settings panel revamp (full edit via /api/settings)

**Status:** `[ ] pending`

**Intent**

Upgrade the existing `/settings` command to open a fullscreen interactive
`SettingsList`-based overlay that allows editing all non-secret `fleet.toml`
policy fields via `PATCH /api/settings`. Show "requires restart" notices for
fields that need it. Persist changes back through the API only.

**Expected Outcomes**

- `/settings` opens a fullscreen `SettingsList` overlay (keyboard-navigable).
- Fields shown: LLM model, max_iters, temperature, turn_timeout_seconds,
  max_tokens, num_retries, cache toggle, recursion_enabled, max_iters,
  verbose, logging level, mlflow tracing toggle.
- Secret fields (`api_key_env`, `base_url_env`, etc.) are not shown or editable.
- "Requires restart" notice rendered next to fields that need it.
- Edits call `PATCH /api/settings` immediately on confirmation.
- Read-only mode (view only) available as fallback if the backend rejects the
  PATCH (e.g. non-loopback client).
- Uses `SettingsList` component from pi-tui (already used in
  `presenter/settings.ts`).

**Todo List**

1. Read `presenter/settings.ts` in full to understand the current
   `SettingsList` usage.
2. Audit the `SettingsPolicyResponse` schema (from `GET /api/settings`) to map
   all editable fields.
3. Determine which fields require restart: all structural config fields do
   (document in code comments); live-editable ones include `verbose` and
   `logging.level`.
4. Update `presenter/settings.ts` to use the new `theme.style()` API and
   render the "requires restart" marker.
5. Map `SettingsPolicyResponse.fields` to `SettingsList` entries with the
   correct input types (toggle, number, text).
6. Wire the save callback to `client.updateSettings(patch)`.
7. Show the response `revision` field after save so the operator sees the
   patch was accepted.
8. Update the `/settings` command handler in
   `commands/skills-settings.ts` to open the new overlay.
9. Run `make tui-check`.

**Relevant Context**

- Current settings presenter: `tools/fleet-tui/src/tui/presenter/settings.ts`.
- API client method: `fleet-api-client.ts` → `getSettings()` / `updateSettings()`.
- Settings API: `GET /api/settings` → `SettingsPolicyResponse`; `PATCH` →
  `SettingsPolicyPatchRequest`.
- Loopback-only restriction: `PATCH /api/settings` requires the client to
  connect from 127.0.0.1 (enforced by backend). The TUI defaults to
  `http://127.0.0.1:8000`, so this is satisfied in normal use.
- `SettingsList` component: available from `@earendil-works/pi-tui` — already
  imported in `presenter/settings.ts`.

---

## Sub-Task 8 — Sessions overlay (fullscreen interactive session browser)

**Status:** `[ ] pending`

**Intent**

Upgrade the `/sessions` command from a transcript-printed list to a fullscreen
interactive overlay with keyboard navigation, search, and live filtering.
Session switching, rename, and archive should be actionable from within it.

**Expected Outcomes**

- `/sessions` opens a `SelectList`-based fullscreen overlay.
- Lists sessions from `GET /api/sessions` with title, status, and last-updated.
- Live search filtering as the user types.
- Arrow keys navigate, Enter selects (resumes that session), Esc closes.
- `r` key renames the selected session inline.
- `a` key archives/unarchives the selected session.
- After session switch, TUI exits and relaunches (same as `--session <id>`
  on the CLI) — or emits a clear instruction to the user to relaunch.
- Uses `SelectList` from pi-tui (already used in `presenter/skill-selector.ts`).

**Todo List**

1. Read `presenter/skill-selector.ts` for the `SelectList` usage pattern.
2. Read `commands/sessions.ts` for the current `/sessions` implementation.
3. Create `presenter/session-browser.ts` with a `SessionBrowserOverlay` that:
   - Fetches sessions from `client.listSessions()` on open.
   - Renders each session as a `SelectList` item with title and status.
   - Wires search input to filter client-side.
   - On Enter: dispatch `session/switch` event or prompt user to relaunch.
   - On `r`: open an inline rename input.
   - On `a`: call `client.updateSession(id, { status: "archived" })`.
4. Update `commands/sessions.ts` `/sessions` handler to open the new overlay
   via `presenter.openSessionBrowser()`.
5. Add `openSessionBrowser()` to `PiCommandPresenter` interface.
6. Run `make tui-check`.

**Relevant Context**

- Current sessions command: `tools/fleet-tui/src/tui/commands/sessions.ts`.
- Skill selector pattern: `tools/fleet-tui/src/tui/presenter/skill-selector.ts`.
- `SelectList` from `@earendil-works/pi-tui`.
- Client methods: `listSessions()`, `updateSession()`, `getSession()`.
- Session resume is currently a full CLI restart (`--session <id>`); keep that
  behavior — show the session ID and instruct the user to relaunch.

---

## Sub-Task 9 — Streaming UX improvements (progress, message type rendering)

**Status:** `[ ] pending`

**Intent**

Improve the visual feedback during streaming: animated progress indicator tied
to the actual run phase, distinct rendering for each message type defined in
Fleet's SSE contract, and cleaner display of tool calls / child progress.

**Expected Outcomes**

- `ActivityComponent` in `screen.ts` shows the exact run phase label from
  `turn_status` chunk (`phase` + `status` + `message`), not just "Preparing Turn".
- Spinner is visually distinct per phase: `preparation` = dots, `running` =
  braille, `cancelling` = X.
- `tool` messages render with: tool name bold, input JSON collapsed by default,
  output/error revealed on fold toggle (already partially done — extend to use
  `theme.style()`).
- `child_progress` messages render a compact progress card with state badge
  (`running`/`completed`/`error`), task label, elapsed time, and fold toggle
  for evidence/gaps.
- `reasoning` messages render in a visually distinct muted block.
- `warning` messages render with a `⚠` prefix in warning color.
- `artifact` messages render with `📦` prefix and file size.
- `usage` summary is visually separated from the conversation (already in
  `execution-summary.ts` — ensure it uses `theme.style()`).
- All streaming text uses the `streaming: true` cursor marker correctly.

**Todo List**

1. Read `message-renderer.ts` in full to understand current rendering for each
   `Message.kind`.
2. Update `ActivityComponent` to read phase/status/message from the store's run
   state and reflect them in the loader label.
3. Add phase-specific spinner frames to `working-icon.ts` or inline in the
   loader callback.
4. Update `message-renderer.ts` rendering for `tool`, `child_progress`,
   `reasoning`, `warning`, `artifact` to use `theme.style()`.
5. Implement fold-by-default for `child_progress` evidence/gaps (heavy content).
6. Ensure `code` and `output` message blocks retain syntax highlighting via
   `syntax-highlight.ts`.
7. Add a streaming cursor (`▋` or `▌`) to in-progress `text` messages where
   `streaming === true`.
8. Run `make tui-check`.

**Relevant Context**

- `message-renderer.ts`: `tools/fleet-tui/src/tui/message-renderer.ts`.
- `execution-summary.ts`, `usage-summary.ts`.
- `working-icon.ts`: current spinner frames.
- `store.ts`: `Run` state includes `phase`, `status`, `detail`, `message` from
  `turn_status` chunks.
- `ActivityComponent`: `tools/fleet-tui/src/tui/screen.ts` lines 172+.

---

## Sub-Task 10 — Validation and cleanup

**Status:** `[ ] pending`

**Intent**

Run the full check suite, fix any remaining issues, verify all generated
contracts are still valid, and apply deslop review to all changed files.

**Expected Outcomes**

- `make tui-check` passes clean (format, lint, typecheck, tests, API check,
  stream check).
- No new warnings.
- No hand-edited generated files.
- All new code follows deslop principles: no speculative abstraction, no
  redundant comments, no dead code, no ad-hoc defensive branches.

**Todo List**

1. Run `make tui-check` from repo root.
2. Fix any remaining type errors, lint warnings, or test failures.
3. Run `make api-check` and `make stream-check` to confirm generated contracts
   are unaffected.
4. Review all changed files against deslop criteria:
   - Remove abstractions with no real responsibility.
   - Inline trivial helpers.
   - Remove speculative flexibility.
   - Remove comments that restate the code.
5. Run `git diff --check` to confirm no trailing whitespace or conflict markers.
6. Report what each check establishes and what it does not certify.

**Relevant Context**

- `make tui-check` runs: `make api-check stream-check tui-format-check tui-lint
  tui-typecheck tui-test`.
- Generated files are in `tools/fleet-tui/src/generated/`; do not edit them.
- Deslop principles from `.pi/prompts/deslop.md`: remove abstractions, inline
  trivial wrappers, tighten types, prefer direct readable code.

---

## Execution Order and Dependencies

```
Sub-Task 1 (audit)
    └─► Sub-Task 2 (bump + compile fix)
            └─► Sub-Task 3 (theme migration)
                    ├─► Sub-Task 4 (keybindings)
                    ├─► Sub-Task 5 (header bar)
                    ├─► Sub-Task 6 (footer revamp)
                    ├─► Sub-Task 7 (settings panel)
                    ├─► Sub-Task 8 (sessions overlay)
                    └─► Sub-Task 9 (streaming UX)
                                └─► Sub-Task 10 (validation + deslop)
```

Sub-Tasks 4–9 are independent of each other once Sub-Task 3 is done. They
can be executed in any order or in parallel reviews, but must each pass
`make tui-check` before the next proceeds.

---

## What this plan does not cover

- Backend changes (routes, SSE contract, generated types).
- Multi-session concurrency or WebSocket upgrades.
- Pi Durable integration (not applicable — Fleet uses its own FastAPI backend).
- Provider/model selection UI beyond surfacing the current model in the header
  and allowing model field editing in the settings panel.
- Persistent sidebar, tab bar, or split-pane layout.
