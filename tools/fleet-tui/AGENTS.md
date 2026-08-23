# Fleet TUI Agent Guide

Root workflow and safety rules remain authoritative from
[AGENTS.md](../../AGENTS.md); this guide narrows them for `tools/fleet-tui/`.

pi-tui-only terminal client. No web frontend, no React, no browser runtime.

## Ownership

| File | Responsibility |
|------|---------------|
| `src/fleet-turn-stream.ts` | Strict SSE stream lifecycle (start → … → terminal → [DONE]) |
| `src/sse.ts` | Frame/chunk validation against `FleetUIMessageChunk` union |
| `src/tui/screen.ts` | Alternate-screen layout: transcript `ScrollView` (follow-end), activity strip, editor, footer |
| `src/tui/live-projection.ts` | Live SSE chunk → store events (`LiveTurnProjector`) |
| `src/tui/durable-projection.ts` | Durable reload turns → store events (`projectDurableTurns`) |
| `src/tui/projection-helpers.ts` | Shared pure helpers / message builders for projection |
| `src/tui/transcript.ts` | Transcript rendering of projected messages |
| `src/tui/store.ts` | Atomic hydration; all state via `dispatch` + pure `reduce` |
| `src/tui/commands.ts` | Slash command facade: import-time registration of all commands in stable `/help` order; re-exports the registry types |
| `src/tui/commands/registry.ts` | Command registry: `CommandContext`/`CommandPresenter`/specs, register/get/list, `parseInput` |
| `src/tui/commands/shared.ts` | Shared command helpers: `appendSystem`, `notifySuccess`, FleetApiError-aware `errorMessage` |
| `src/tui/commands/sessions.ts` | `/sessions`, `/rename`, `/resume`, `/reload` (durable hydration) |
| `src/tui/commands/skills-settings.ts` | `/skills`, `/skill`, `/settings`, `/profiles`, including loopback-only TOML policy editing |
| `src/tui/commands/files-artifacts.ts` | `/volume`, `/attach`, `/files`, `/file`, `/artifact(s)`, `formatVolumeTree` |
| `src/tui/commands/status-theme-misc.ts` | `/help`, `/clear`, `/cancel`, `/status`, `/redo`, `/trace`, `/theme`, `/exit` |
| `src/tui/command-presenter.ts` | `PiCommandPresenter` (interactive overlays) + facade re-exports for presenter modules |
| `src/tui/presenter/overlay.ts` | Overlay scaffold: `SelectOverlay`, `TitledComponent`, title/hint helpers, `OVERLAY_OPTIONS` |
| `src/tui/presenter/settings.ts` | Settings rows/editors: `fieldItem`, `parseFieldValue`, `TextSettingEditor`, `MultiChoiceEditor` |
| `src/tui/presenter/skill-selector.ts` | `SkillSelector` multi-select picker for `/skills` pinning |
| `src/tui/draft-store.ts` | Debounced per-Session draft/selection persistence (`FLEET_TUI_STATE_DIR`) |
| `src/tui/themes/palette.ts` | Theme token types, builtin dark/light palettes, custom JSON themes + watcher |
| `src/tui/theme.ts` | Theme engine: adaptive surfaces, selection, `initTheme`/`setTheme`, pi-tui theme factories |
| `src/tui/keybinding-hints.ts` | Formatted key hints (Esc/↑↓/PgUp) for footers and fold hints |
| `src/tui/working-icon.ts` | Shared `◇ ◈ ◆ ◈` still-working pulse frames |
| `src/generated/openapi.ts` | **Generated** — do not hand-edit; use `make api-sync` |
| `src/generated/fleet-ui-chunk-validation.ts` | **Generated** — do not hand-edit; use `make api-sync` (`scripts/generate_tui_chunk_validation.py`) |

## Validation

```bash
make tui-check        # api-check + format:check + lint + typecheck + test (Vitest)
```

Individual lanes: `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm format:check`.

## Constraints

- Alternate-screen viewport: the transcript is a follow-end `ScrollView` inside
  `TuiAltScreen`; PgUp/PgDn scroll a page, Home/End jump top/bottom, the mouse
  wheel scrolls, drag selects text for copy, and new output re-follows the end.
- Single client protocol: the AI SDK UI v1 stream projected by
  `src/fleet_rlm/api/sse.py`, with HTTP types owned by `make api-sync`. Do not
  design a second client protocol unless a second client exists.
- State mutations exclusively through `store.dispatch`; no direct mutation.
- SSE ordering invariants are enforced in `streamFleetTurn` — one start, one terminal, [DONE] last.
- RLM progress is projected from typed Runtime Events: Root iterations expose
  callback/trajectory reasoning, code, and output, while recursive status stays
  bounded backend metadata. Do not infer recursive depth from iteration counts,
  model text, or `dspy.RLM(verbose=...)`; the backend defines Root depth 0,
  native child depth 1, and Sub-LM fallback beyond that boundary.
- API errors use `FleetApiError` with `status`, `correlationId`, `code`.
- Keep focused tests beside their source level under `src/tests/` or
  `src/tui/tests/`; shared behavior may be covered through its owning feature.
- `/settings` reads and edits non-secret `config/fleet.toml` policy only; it
  never displays `.env` values and saved changes require a Fleet restart. The
  interactive editor stays open across successive field edits, saving through a
  `SettingsSaveCallback` that returns the freshest policy (PATCH response, or a
  GET refresh after a `settings_revision_conflict`); environment-overridden and
  singleton `single_choice` fields render read-only, and number fields reject
  non-numeric input before any PATCH.
- `/profiles` selects the Fleet profile used on the next restart by PATCHing
  `config.default_profile` through loopback policy; the picker marks the
  currently running profile separately from the selected restart target, and
  the active profile changes only after a Fleet restart.
- Interactive one-shot command successes (settings saved, profile selected,
  theme applied, Skill selections updated) go through `CommandContext.notify`
  (wired to `TuiAltScreen.flash`) as transient flashes; without a `notify`
  callback (tests, non-interactive contexts) the same text falls back to a
  system transcript message. Failures always stay in the transcript. Command
  overlays share the `SelectOverlay`/`TitledComponent` title, context, and
  bottom-hint pattern in `presenter/overlay.ts` (`SelectOverlay` and the
  settings/skill presenters are re-exported via `command-presenter.ts` for
  tests).
- Themes: builtins are `themes/palette.ts`; custom JSON themes live in
  `$FLEET_TUI_STATE_DIR/themes/*.json` (token overrides with optional `vars`),
  hot-reload via watcher, selected name persisted to `$FLEET_TUI_STATE_DIR/theme`.
- Requires Node ≥ 22.19.0 and pnpm.
