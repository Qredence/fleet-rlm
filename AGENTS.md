# Fleet RLM — Agent Instructions

`fleet-rlm` is an RLM-native system built around DSPy, Daytona, FastAPI, durable Sessions/Turns, and a maintained terminal client under `tools/fleet-tui/`.

This file defines repository-wide execution rules. `tools/fleet-tui/AGENTS.md` adds rules specific to the terminal client.

## Working model

Before changing code:

1. Inspect the relevant implementation and its tests.
2. Search for existing implementations, helpers, and established boundaries before introducing new abstractions.
3. Read `ARCHITECTURE.md` when the task affects ownership, lifecycle, dependencies, domain boundaries, or cross-component behavior.

Treat current code, tests, `config/fleet.toml`, dependency pins, generated-contract checks, and executable validation as authoritative; documentation does not override executable contracts.

## Execution

- Treat action requests as instructions to implement and validate through completion or a concrete blocker. Resolve routine, reversible choices from repository evidence and user intent.
- For simple/local tasks, act directly; avoid long plans, routine tool narration, speculative exploration, and questions that repository evidence can answer.
- Ask only when missing information materially changes the outcome or additional authority is needed. Prepare authorized, reviewable work before asking; do not ask again for authority already granted.
- Keep changes focused, reuse existing modules, and prefer the simplest complete implementation. Remove obsolete code only when safe, verified, and directly relevant. Do not clean up unrelated files.
- For architectural work, keep a concise task list and validate meaningful stages. Use `uv run` for Python commands.
- Treat follow-ups as steering unless the user cancels or replaces the task. Answer status questions briefly, then continue.
- Delegate only when explicitly requested or required by applicable instructions; assign bounded responsibilities and preserve others' edits.

## Instructions and communication

- Explicit user instructions take precedence over skill guidelines, subject to system and developer instructions. Apply skills to the actual task; guidance edits do not themselves require API credentials or live calls.
- Distinguish the coding agent's authentication/model from Fleet's runtime provider configuration. Do not change either merely because the other is discussed.
- If a skill blocks progress, cite its exact file and instruction, explain why it applies, and state the input needed. Do not infer extra approval requirements.
- Use concise, plain language. Lead with results and evidence; state limits. Prefer short paragraphs and useful lists; avoid repeated summaries, stock phrases, and unnecessary formatting.

## Git and external effects

Preserve pre-existing staged, unstaged, and untracked changes. Do not reset, clean, stash, overwrite, or revert changes you did not make.
Do not commit, amend, push, open pull requests, deploy, publish, mutate shared infrastructure, or perform other externally visible actions unless explicitly requested.
Never expose credentials, tokens, `.env` values, provider secrets, or raw infrastructure errors.

## Hard architecture invariants

- `src/fleet_rlm/` is the canonical Python backend.
- Keep FastAPI routes as transport adapters; obtain application/runtime dependencies through the established composition/dependency seams.
- Use `dspy.LM` as Fleet's LLM abstraction. Do not introduce direct LiteLLM application usage.
- Use the repository-pinned DSPy implementation and current official DSPy documentation as the behavioral contract when DSPy/RLM behavior matters.
- DSPy owns native RLM history and trajectory semantics. Fleet must not independently duplicate, compact, truncate, reset, or reconstruct native `REPLHistory`.
- Treat process-scoped LM instances as immutable templates. Turn-specific deadlines, retries, adapters, callbacks, or other mutable execution state must be isolated per Turn.
- Turn ownership and deadlines bound LM, Tool, interpreter, and recursive work. Do not allow detached work to continue mutating Fleet state after settlement.
- Recursive delegation depth is distinct from native RLM iteration count.
- Keep Daytona SDK integration inside `src/fleet_rlm/daytona/`.
- Keep internal Runtime Events transport-neutral. Public clients consume the backend stream contract rather than defining parallel execution semantics.
- State transitions, settlement, persistence, and resource cleanup must go through their owning lifecycle/service abstractions.
- Alembic owns live schema evolution.
- `config/fleet.toml` owns runtime policy and profile configuration. Do not encode current provider/model choices in application code or repository instructions.
- Secrets may only come from explicitly configured environment references and must never be exposed through settings APIs, traces, logs, SSE payloads, or public exceptions.

See `ARCHITECTURE.md` for component ownership and dependency structure.

## Generated artifacts

Do not hand-edit generated contracts or generated client types, including:

- `openapi.yaml`
- `tools/fleet-tui/src/generated/openapi.ts`
- generated TUI stream/chunk validation artifacts

When their source contract changes, regenerate them using repository commands and include the generated changes.

## Validation

Use the smallest validation lane that proves the change, then escalate when the affected contract requires it.
Repeat or broaden passing checks only for new changes, failures, or unresolved concerns. Add tests for meaningful behavior or regression risks, not to restate implementation.
For documentation or agent-instruction-only changes, run `make check-docs` and `git diff --check`. Code lanes below apply when their code or executable contracts change.

### Focused Python changes

Run relevant tests and checks: `uv run pytest <relevant-tests> -q`, `uv run ruff check <changed-paths>`, and `uv run ruff format --check <changed-paths>`.

Run `uv run ty check src` when typed application interfaces or implementations change.

### API or generated contracts

After changing public HTTP schemas, routes, settings contracts, stream contracts, or generated client interfaces:

```bash
make api-sync
make api-check
```

### TUI

For code changes under `tools/fleet-tui/`:

Run `make tui-check`.

Follow `tools/fleet-tui/AGENTS.md`.

### Architecture or dependency boundaries

When moving responsibilities, imports, provider integrations, or package boundaries:

```bash
make check-codebase-tree
make check-dependency-boundaries
```

### Broad or cross-cutting changes

Run `make check` for changes spanning multiple subsystems, lifecycle semantics, public contracts, configuration resolution, or other repository-wide invariants.

### Release or security work

When the task affects these concerns or release-ready validation is explicitly requested:

```bash
make check-security
make build-release
make check-release
git diff --check
```

### Live validation

Credentialed Daytona, model-provider, database, benchmark, and other live tests are explicit operator actions.

Do not enable or infer live credentials merely to satisfy ordinary validation.

When live validation is requested, use the existing repository live-test or verification entry points and report exactly what was exercised.

## Completion

Before finishing:

1. Review the diff for unintended changes.
2. Run every validation lane applicable to the touched contract.
3. Run `git diff --check`.
4. Report what changed, what was validated, and anything that could not be validated.

Do not claim live, release, security, or integration guarantees from narrower tests alone.
