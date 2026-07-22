# Repository Agent Map

`fleet-rlm` is a backend-first adaptive recursive language model workspace built around a Daytona-backed DSPy RLM runtime. The prior Web frontend has been removed; the maintained development client is `tools/fleet-tui/`.

## Operating Model

- Use closest applicable `AGENTS.md` before editing files; deeper guides override this map.
- This guide describes the current checkout. Local `PLANS.md` records verified
  phase outcomes and remaining promotion evidence; current code, tests, generated
  contracts, and tracked docs remain authoritative.
- Keep repo docs, generated contracts, and `.codex/` actions aligned with implementation changes.
- Prefer smallest validation lane that covers the change, then escalate when contracts move.
- Do not hand-edit generated/synced artifacts; use the commands listed below.
- Do not mutate user-level Codex config. Ask before deploy, push, migrations, or deletion.

For the current Codex Cloud delivery sequence, use `dev-0.7` as the base branch and never use `main` or `master`. Cloud tasks may use limited internet and explicitly authorized apps/connectors; keep credentials and tokens out of the repository.

## Reading Path

1. `docs/agent-harness/README.md` - harness model, reading order, and quality bar.
2. `docs/agent-harness/feedback-loop.md` - local Codex loop and report expectations.
3. `docs/agent-harness/architecture-invariants.md` - backend and generated-file rules.
4. `docs/reference/codebase-map.md` - source layout and ownership map.
5. `docs/how-to-guides/testing-strategy.md` - validation lanes by change type.

## Deeper Agent Guides

- `src/fleet_rlm/AGENTS.md` - backend, runtime, API, persistence, Daytona, and package rules.

## Durable Detail Locations

- Auth, DB, SSE, runtime, and deploy details live in `src/fleet_rlm/AGENTS.md` or matching docs.
- Local Codex actions, ports, terminal-client checks, and tool preferences live in `.codex/` and loop docs.

## Agent skills

### Issue tracker

Planning issues use local Markdown under `.scratch/<feature>/`; `.scratch/` is local-only and ignored. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix` roles. See `docs/agents/triage-labels.md`.

### Domain docs

Use the root and backend contexts listed in `CONTEXT-MAP.md`, plus relevant ADRs. See `docs/agents/domain.md`.

## Setup

For ordinary local development:

```bash
uv sync --all-extras --dev
```

Codex Cloud workspaces use `zsh .codex/workspace-bootstrap.zsh`; the script
installs locked dependencies and enforces the Cloud branch guard.

## Run

```bash
uv run fleet cli
uv run fleet deno
uv run fleet doctor daytona
uv run fleet web
uv run fleet-rlm serve-api --port 8000
```

## Validation

```bash
make check
```

The parallel backend test lane defaults to at most two pytest-xdist workers.
Override it only on a runner with verified capacity using
`make test PYTEST_XDIST_MAX_WORKERS=<count>`.

## Generated Artifacts

Do not hand-edit `openapi.yaml` or
`tools/fleet-tui/src/generated/openapi.ts`.

Use: `make api-sync`, `make api-check`.

## Drift Checks

Run `make check-docs` when docs, commands, Codex config, generated contracts, or scripts change.

## Learned User Preferences

- Always use the `zsh` terminal profile for CLI commands; prefer running Python scripts/commands using `uv run` over raw `python3` or `python`.
- Secure production deployments strictly on Bring-Your-Own-Key (BYOK) model; never leak server-level secrets (like Gemini API keys or Daytona keys) to authenticated users.
- Do not edit `.plan.md` or any attached implementation plans while executing a task, prioritizing marked-in-progress to-dos sequentially.
- Run the full validation gate (`make check`, `make test-deno`,
  `make check-security`, `make build-release`, `make check-release`, and
  `git diff --check`) before commits when the user or phase completion requires it.
- Do not amend commits already pushed to remote; use narrow follow-up commits for fixes discovered after push.
- Never expose provider credentials through Fleet-RLM API requests; sanitize client-facing prepare/startup errors—never expose raw `str(exc)`, stack traces, credentials, or Daytona/provider internals to API clients.
- Do not commit `AGENTS.md` unless changes are intentional team workflow guidance; continual-learning workspace-fact deltas may stay uncommitted.
- Avoid introducing direct `litellm` usage in application code; reach LLM providers through `dspy.LM` instead.
- Prefer wire-protocol-named Literal unions (`openai_responses`, `openai_chat_completion`, `anthropic_messages`) over vendor-flavored or `_compatible`-suffixed provider-type enums, and keep LLM profiles flat (profile name, provider type, base endpoint, API key) rather than over-abstracting.
- Cite ONLY DSPy (installed 3.3.0b1 source + dspy.ai docs) as the reference contract for LLM/runtime design; do NOT cite the `/daytona` or `daytona-signature` skill as authority for DSPy/RLM decisions. For Daytona sandbox/interpreter and FastAPI API work, use the `/daytona` and `/fastapi` skills for provider/framework best practices.
- When asked for a plan, make it code-tree-explicit: exact file paths, line ranges, and ADD/REMOVE/EDIT tables; include expected behavior, capability, and code-change impacts; and cite DSPy, Daytona, and/or FastAPI docs when justifying relevance — not generic prose. When grilling or collecting decisions, prefer AskUser/AskQuestion over long inline multi-question dumps when that tool is available.
- Prefer live per-iteration RLM reasoning on the existing SSE/`RLMReasoning` → TUI path; treat `dspy.RLM(verbose=…)` as host-logger-only and insufficient for operator-visible streaming.

## Learned Workspace Facts

- Local development runs on `:8000`. `fleet cli` supervises Daytona plus pi-tui,
  `fleet deno` supervises Deno plus pi-tui, and `fleet web` or `fleet-rlm serve-api`
  remains backend-only. Supervised backend logs live under `.fleet_rlm/logs/`;
  `fleet doctor daytona` is the opt-in disposable provider/mount probe.
  `POST /api/sessions/{session_id}/turns`
  requires `Idempotency-Key` and projects typed `RuntimeEvent` values over SSE;
  the legacy top-level chat, `/api/v1`, and WebSocket surfaces are removed.
- `src/fleet_rlm/` is the canonical RLM-native backend. The parallel foundation package was cut over after exit-bar evidence on `71e79271`; there is no compatibility runtime or dual-serve path.
- The canonical public Run Environment set is `deno` and `daytona`. Private tests install a credential-free deterministic composition explicitly. Deno is intentional local vanilla `dspy.RLM` (real LM + DSPy default Deno/Pyodide) with Attachment reads and Skills but no durable Artifact promotion; Daytona is the full Fleet path (Sandbox, Workspace Volume Scope, Artifact promotion).
- `create_app()` installs handlers, routers, and the static in-memory bundled
  Skill catalog (including `dspy-rlm`, which defines `dspy.RLM` as Recursive
  LM/REPL — never RAG/`dspy.Retrieve`). FastAPI lifespan installs one complete
  Deno or Daytona runtime inventory through `composition/`; routes retrieve
  composed runtime modules.
- The maintained terminal uses pi-tui only. `fleet-turn-stream.ts` owns strict stream lifecycle, `sse.ts` owns frame/chunk validation, `tui/projection.ts` owns live/reload projection, and `tui/store.ts` owns atomic hydration. The monochrome operator timeline renders all evidence statically expanded in native terminal scrollback; Fleet does not capture the mouse or maintain a transcript viewport. Live mid-turn evidence is tools plus Daytona interpreter code/output; `RLMReasoning` is usually filled after `rlm.acall` from the native trajectory; `dspy.RLM(verbose=…)` is host-logger-only and does not feed RuntimeEvents.
- Live Daytona MVP proof (`tests/live/backend/`, `scripts/live_daytona_verify.py`) loads repo `.env` via `python-dotenv` with `override=False`; existing process exports still win.
- Daytona SDK imports are confined to `fleet_rlm.daytona`. Durable Attachments
  and Artifacts use Workspace Volume Scope; Session Workspace is
  append/update-only (list/stat/read/write with overwrite; no delete Tool).
  Long operator reports should write Session Workspace then `SUBMIT` a short
  summary; oversized `SUBMIT` fails with public Turn output budget errors.
  Volume backends that reject atomic `os.replace` use a non-atomic overwrite
  fallback (keep new content if only file `fsync` fails).
  `TurnLifecycle.finish()` promotes Artifact Candidates and owns atomic Turn
  Commit, while `TurnCoordinator` owns stream settlement, terminal ordering,
  and cleanup.
- `read_session_history` enforces a fixed 256 KiB UTF-8 aggregate byte budget on
  returned message bodies (host constant, not a `FLEET_*` setting), using
  whole-message omission with `truncated` / `bytes_returned` / `byte_budget` /
  `skipped_ordinal` continuation metadata.
- Settings use only `FLEET_*`. The local BYOK API uses one deterministic process-local User and Workspace scope and accepts no Authorization or synthetic identity headers.
- Alembic owns the live schema through one fresh canonical baseline. `create_tables` is restricted to explicit SQLite test/offline helpers; run `alembic check` against an upgraded empty database for drift.
- Repository validation is `make check`; its default test targets mask local
  live credentials, install private deterministic composition explicitly, and
  include the maintained TUI. Live promotion uses `tests/live/backend/` with
  explicit `FLEET_LIVE=1`. `make api-sync` owns root OpenAPI and generated TUI
  HTTP types; a future graphical client is separate work.
- Under DSPy 3.3.Xb, every model passed to `dspy.LM` must resolve a provider.
  Canonical defaults are `openai/gpt-4o-mini`; bare model ids are accepted for
  OpenAI-compatible bases and normalized by `normalize_model_id`. Prefer stock
  `dspy.LM` with stateless call/context overrides over stateful copies or custom
  LM wrappers. Pinned `dspy==3.3.0b1` RLM uses `max_iterations` (not
  `max_iters`). DSPy private-protocol knowledge stays behind `rlm.dspy_contract`
  (construction/usage) and `rlm.dspy_interpreter_contract` (inject/`FinalOutput`).
