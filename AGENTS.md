# Fleet RLM — Agent Guide

Fleet is a Session-first FastAPI/SSE application. DSPy owns the RLM loop,
Daytona supplies isolated execution environments, and `tools/fleet-tui/` is the
maintained terminal client. This guide records operating rules; it is not a
feature roadmap or a substitute for executable behavior.

## Read the current system

- Inspect the affected code, tests, configuration, and generated contracts
  before choosing a design. Current behavior and tests outrank historical plans
  and receipts.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for durable owners and boundaries,
  [CONTRIBUTING.md](CONTRIBUTING.md) for setup, [docs/index.md](docs/index.md)
  for task guidance, and [tools/fleet-tui/AGENTS.md](tools/fleet-tui/AGENTS.md)
  for terminal-client work.
- Use [scripts/README.md](scripts/README.md) as the inventory of retained script
  commands, support modules, and data; `make check-docs` runs the unified
  repository-hygiene checks for that inventory and active repository guidance.
- Update architecture guidance when a durable owner or trust boundary changes.
  Keep sequencing, task status, and dated evidence in the relevant plan or
  ledger rather than duplicating a roadmap here.

## Preserve ownership and guarantees

- `src/fleet_rlm/` is canonical. API routes validate and project transport;
  they obtain services through application composition.
- Prefer the pinned native `dspy.RLM` and `dspy.LM`. DSPy owns its reasoning
  loop, `REPLHistory`, and trajectory semantics. Do not add another loop,
  planner, model router, or permanent compatibility layer.
- Use ordinary Python for deterministic inspection, parsing, and reduction;
  native `llm_query` calls for bounded semantic work; Fleet child RLMs only for
  independent investigations that need their own iterative work. These are
  distinct capabilities, not a mandatory workflow.
- Keep full-child recursion bounded to one child level. The configured
  `daytona-native` profile is the default; `daytona-recursive` is opt-in while
  comparative evidence has not established a reason to promote children.
  Preserve the shared Turn budget, bounded admission, and ordered partial
  outcomes.
- Resolve child inputs under existing Session authority, stage bounded copies
  in the child's private scratch, and harvest validated results before cleanup.
  Do not grant children root-owned memory, task checkpoints, publication, or
  credentials. Do not attach the parent workspace read-write to the active
  semantic-child path.
- Keep mutable execution state scoped to a Turn or invocation. Durable
  conversation, task progress, files, and memory belong to their existing
  owners; Python variables are not durable state. A child finding is a
  candidate for Root verification, not a committed conclusion.
- `DaytonaRuntime` owns provider resources and cleanup. The Turn coordinator
  owns claim, preparation, execution, settlement, and commit. Late work cannot
  mutate settled state, and unresolved containment cannot become successful
  settlement.
- Keep Runtime Events transport-neutral until API projection. MLflow is
  optional observation and evaluation, never execution authority. Skills add
  strategy and manifested resources, never tools, permissions, or budgets.
- Preserve the Phase 5 network-policy waiver as recorded. A policy request or
  one successful canary does not establish network isolation, provider mount
  enforcement, timeout containment, or comparative quality.
- Keep policy in `config/fleet.toml`. Alembic owns live schema changes. Do not
  change dependency pins, models, or production resource defaults as an
  unmeasured side effect of another task.

## Generated contracts

| Generated artifact | Regenerate / verify |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync` / `make api-check` |
| TUI stream fixtures and validators | `make stream-sync` / `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix` / `make check-docs` |

Never hand-edit generated artifacts. Bundled Skill Markdown is shipped runtime
content; inspect its catalog, manifests, resources, and contract tests when
changing it.

## Work and validate safely

- Preserve unrelated staged, unstaged, and untracked work. Do not reset, clean,
  stash, overwrite, or revert work outside the authorized scope.
- Commits, pushes, pull requests, deployment, publication, and shared
  infrastructure changes need an explicit request. Provider, Daytona,
  database, and benchmark runs use their documented entry points and require
  explicit operator authorization.
- Use `uv run` for Python.
- Review the final diff and run `git diff --check`. State what the checks prove:
  local tests do not certify provider behavior, release readiness, or promotion.

| Change | Minimum validation |
| --- | --- |
| Documentation or guidance | `make check-docs` |
| Focused Python | Relevant `uv run pytest -q`, `uv run ruff check`, and `uv run ruff format --check` |
| Typed Python interfaces | Also `uv run ty check src` |
| TUI | `make tui-check` |
| API, settings, streams, generated interfaces | Regenerate, then `make api-check` and `make stream-check` as applicable |
| Boundaries or integrations | `make check-codebase-tree` and `make check-dependency-boundaries` |
| Cross-cutting lifecycle or configuration | `make check` |
| Release or security scope | `make check-security`, `make build-release`, and `make check-release` |
