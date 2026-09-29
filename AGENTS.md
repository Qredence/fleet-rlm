# Fleet RLM — Agent Guide

Fleet is a Session-first FastAPI/SSE application. DSPy owns the RLM loop,
Daytona runs generated Python in isolated Sandboxes, and `tools/fleet-tui/` is
the maintained terminal client. This guide sets working rules; executable
behavior and tests define what the system does.

## Orient to the task

- Inspect the affected code, tests, configuration, and generated contracts
  before deciding how to change behavior. Preserve unrelated staged, unstaged,
  and untracked work.
- Use [ARCHITECTURE.md](ARCHITECTURE.md) for durable ownership and trust
  boundaries, [source layout](docs/reference/source-layout.md) for the package
  map, [docs/index.md](docs/index.md) for task-specific references, and
  [CONTRIBUTING.md](CONTRIBUTING.md) for setup. [scripts/README.md](scripts/README.md)
  inventories retained repository helpers and data.
- Put regressions in the existing behavior-owning test suite by default. Use
  the [testing strategy](docs/how-to-guides/testing-strategy.md) to select the
  suite and decide when a separate test file is warranted.
- Read [tools/fleet-tui/AGENTS.md](tools/fleet-tui/AGENTS.md) for client work.
  Update architecture guidance when a lasting owner or trust boundary changes;
  keep sequence, status, and dated evidence in plans or receipts.

## Keep responsibilities in their owners

- `src/fleet_rlm/` is the canonical backend. Application composition builds
  route services; API routes validate requests and project transport.
- Use the pinned native `dspy.RLM` and `dspy.LM`. DSPy owns its reasoning loop,
  `REPLHistory`, and trajectory. Do not add a parallel loop, planner, model
  router, or permanent compatibility layer.
- Use Python for deterministic inspection and reduction, `llm_query` for
  bounded semantic judgments, and Fleet child RLMs for independent work that
  needs iterative investigation. These tools serve different needs.
- Full child recursion is bounded to one level. `daytona-native` is the
  configured default; `daytona-recursive` opts into Fleet children. Preserve
  the shared Turn finalization ledger, the bounded admission of Tool calls,
  recursive children, and execution output, and ordered partial outcomes.
  Provider-attempt admission and per-Turn LM deadlines are not preserved:
  Fleet's LMs are stock `dspy.LM`, and DSPy owns their retries.
- Resolve child inputs under Session authority. The active `semantic-child`
  path stages bounded copies in private scratch, validates outputs before
  cleanup, and stays Volume-less. The separate `workspace-child` profile
  supports child work that needs durable files through a mount scoped to
  `workspaces/<workspace_id>`. Keep task checkpoints, memory tools, publication,
  and credentials under their existing owners; Root verifies child findings.
- Keep invocation state scoped to a Turn. Sessions own committed conversation
  and task checkpoints; Workspace services own durable files and memory.
  `DaytonaRuntime` owns provider resources and cleanup; `TurnRuntime`
  coordinates claim through cleanup; settlement and persistence own commit.
  Late work cannot change settled state, and unresolved containment cannot
  settle successfully.
- Keep Runtime Events transport-neutral until API projection. MLflow is
  optional observation, not execution authority. Skills supply strategy and
  manifested resources, not tools, permissions, budgets, or scheduling rights.
- Keep policy in `config/fleet.toml` and live schema changes in Alembic. Do not
  change dependency pins, models, or production resource defaults as an
  unmeasured side effect. Honor the public-network policy waiver in the
  [Daytona Snapshot guide](docs/how-to-guides/daytona-snapshot.md); a policy
  request or one canary does not prove provider enforcement or quality.

## Generated contracts

| Artifact | Source command | Check command |
| --- | --- | --- |
| `openapi.yaml`, generated TUI HTTP types | `make api-sync` | `make api-check` |
| TUI stream fixtures and validators | `make stream-sync` | `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix` | `make check-docs` |

Never hand-edit generated outputs. Bundled Skill Markdown ships at runtime;
check its catalog, manifests, resources, and contract tests when changing it.

## Validate and deliver safely

- Use `uv run` for Python. Use the TUI's pinned Node and pnpm versions from
  `tools/fleet-tui/`; run its pnpm commands from that package directory.
- Run the focused lane for the change:

| Change | Validation |
| --- | --- |
| Documentation or agent guidance | `make check-docs` |
| Python behavior | Focused `uv run pytest -q`, `uv run ruff check`, and `uv run ruff format --check` |
| Typed Python interfaces | Also `uv run ty check src` |
| TUI | `make tui-check` |
| API, settings, streams, or generated interfaces | Regenerate affected outputs; run `make api-check` and/or `make stream-check` |
| Ownership boundaries or integrations | `make check-codebase-tree` and `make check-dependency-boundaries` |
| Cross-cutting lifecycle or configuration | `make check` |
| Release or security changes | `make check-security`, `make build-release`, and `make check-release` |

- Review the final diff and run `git diff --check`. Report what checks establish:
  local tests do not certify provider behavior, release readiness, or promotion.
- Do not reset, clean, stash, overwrite, or revert unrelated work. Commits,
  pushes, pull requests, deployment, publication, and shared infrastructure
  changes require an explicit request. Provider, Daytona, database, and
  benchmark runs require their documented entry point and operator authorization.
