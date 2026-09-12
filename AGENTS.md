# Fleet RLM — Agent Instructions

Fleet is a DSPy/Daytona backend with durable Sessions/Turns and a pi-tui client.
This guide owns repository workflow and validation selection.

## Task context

- Inspect the affected implementation and tests; reuse existing ownership seams.
- For ownership, lifecycle, or dependency changes, consult [ARCHITECTURE.md](ARCHITECTURE.md).
- For terminal work, apply [tools/fleet-tui/AGENTS.md](tools/fleet-tui/AGENTS.md).
- For setup, use [CONTRIBUTING.md](CONTRIBUTING.md); find task-specific guides through [docs/index.md](docs/index.md).
- Current code, tests, pinned dependencies, `config/fleet.toml`, and generated checks outrank proposals and dated receipts.

## Execution and authority

- Complete authorized local work and affected checks, fixing introduced failures without repeated approval; stop at completion or a concrete blocker.
- Preserve unrelated staged, unstaged, and untracked work; never reset, clean, stash, overwrite, or revert others' changes.
- Commits, amendments, pushes, PRs, deployment, publication, and shared-infrastructure changes require explicit requests.
- Credentialed provider, Daytona, database, and benchmark runs are explicit operator actions; use existing entry points and report their evidence scope.
- Use `uv run` for Python. Delegate only when explicitly requested or required by applicable instructions.
- User instructions take precedence over skill guidance, subject to system/developer instructions. If a skill blocks work, cite its exact instruction and required input.
- Coding-agent authentication/model and Fleet runtime provider configuration are separate; discussing one does not authorize changing the other.

## Architecture constraints

- `src/fleet_rlm/` is canonical. FastAPI routes use composition/dependency seams as transport adapters.
- Use native `dspy.RLM` and `dspy.LM`, with the pinned implementation and official DSPy documentation; no direct application LiteLLM usage.
- DSPy owns native `REPLHistory` and trajectory semantics; Fleet must not duplicate, compact, truncate, reset, or reconstruct them.
- Process-scoped LMs are immutable templates; isolate mutable deadlines, retries, adapters, and callbacks per Turn.
- Turn ownership/deadlines bound LM, Tool, interpreter, and recursive work through settlement; detached work must not mutate settled state.
- Recursive delegation depth is distinct from native RLM iteration count.
- Retained broker execution is the production path; native semantic queries remain available. Fleet child RLM tools require explicit profile opt-in.
- Consult the [ADR 006 ledger](docs/decisions/006-implementation-status.md) before claiming cutover, containment, recursive value, or live certification.
- Daytona SDK integration stays in `src/fleet_rlm/daytona/`; internal Runtime Events remain transport-neutral.
- Clients consume backend contracts. State transitions, persistence, settlement, and cleanup use their owning services; Alembic owns live schema evolution.
- `config/fleet.toml` owns runtime policy; keep current provider/model choices out of application code and instructions.
- Secrets come only from configured environment references; never expose values, credentials, or raw infrastructure failures in settings, logs, traces, SSE, or public errors.

## Generated content

Never hand-edit generated contracts or client types. Change their owning sources:

| Artifact | Regenerate / verify |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync` / `make api-check` |
| TUI stream/chunk validation artifacts | `make stream-sync` / `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix` / `make check-docs` |

Bundled Skill Markdown is runtime content: inspect its catalog and contract tests when editing it.

## Validation selection

Use affected lanes below; repeat passing checks only for new changes or unresolved concerns.
Add regressions to existing behavior-owning tests; see [testing strategy](docs/how-to-guides/testing-strategy.md) for fixtures and suite boundaries.

| Changed contract | Required lane |
| --- | --- |
| Documentation / agent instructions only | `make check-docs` |
| Focused Python | `uv run pytest <relevant-tests> -q`; `uv run ruff check <changed-paths>`; `uv run ruff format --check <changed-paths>` |
| Typed application interfaces or implementations | Also `uv run ty check src` |
| TUI code | `make tui-check` |
| HTTP schemas/routes, settings, streams, generated interfaces | Regenerate affected contracts above; `make api-sync` and `make api-check` |
| Ownership, imports, integrations, package boundaries | `make check-codebase-tree`; `make check-dependency-boundaries` |
| Multiple subsystems, lifecycle, public contracts, configuration resolution | `make check` |
| Release/security work or release-ready validation | `make check-security`; `make build-release`; `make check-release` |

Guidance-only changes need no live backend or credentials. Local checks do not establish live certification.

## Completion

Review the diff, run applicable checks and `git diff --check`, and report changes, results, and blockers.
State evidence limits; narrow checks do not establish broader live, release, security, or integration guarantees.
