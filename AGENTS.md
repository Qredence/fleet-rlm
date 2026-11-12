# Agent instructions

Fleet RLM Lite is a Python application using Textual, FastAPI/SSE, DSPy, Daytona,
PostgreSQL, and MLflow/GEPA. Keep changes scoped, reuse existing abstractions, and
preserve the contracts below.

## Start here

1. Run `git status --short` and inspect the existing diff. Preserve uncommitted
   work, including deletions.
2. Consult the [source layout](docs/reference/source-layout.md), relevant source,
   [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock). Read
   [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries.

## Working rules and authorization

- Use Python 3.11–3.13, `uv`, four-space indentation, and Ruff. Prefer `pnpm` for
  JavaScript.
- Use native DSPy directly. Do not duplicate its mechanisms, add speculative
  abstractions, or introduce compatibility aliases.
- Never commit credentials or `.env` files. Treat traces and artifacts as sensitive.
- Commit, push, publish, and deploy only with explicit authorization. Use
  conventional commit prefixes.

## Runtime contracts

### Routing and execution

- Execute generated Python in Daytona; keep credentials and host-mediated tools
  on the server. Preserve shared Turn budgets, isolated child sandboxes,
  success-only artifact promotion, and provenance.
- Use scoped `dspy.context` and preserve native tracing, evaluation, and
  serialization. Validate pinned integration contracts on dependency upgrades.
- **Exception:** evaluate rescore/synthetic jobs set `dspy.configure` callbacks
  inside their one-job worker process because MLflow scorer threads do not
  inherit `dspy.context`.

### Turn lifecycle

- Keep one terminal outcome and one teardown owner per Turn.
- On failure, cancellation, expiry, or disconnect before an outcome: fence host
  work, shut down children, and stop the root before acknowledgment. Success
  retains the warm root.
- Preserve execution reply fences and generation-bound watchdogs serialized with
  Turn end/reset. Fencing alone neither interrupts providers nor stops children.

### Persistence and workspace

- Preserve snapshot resource floors, provider-resolved provenance, historical
  `pre-provenance` readability, Store migrations, and runtime lease fencing.
  Stored history cannot restore live interpreter state.
- Keep `workspace_state` (`known_empty` / `unknown`) lease-fenced. Invalidate it
  to `unknown` before Daytona Volume mutations.
- Preserve local Skill files in `known_empty` Direct replays.

### Network access

Keep ordinary API access loopback-only by default. Non-loopback binding requires
explicit opt-in and a separately secured boundary for external exposure.

References: [HTTP API](docs/reference/http-api.md),
[configuration](docs/reference/configuration.md),
[sandbox](docs/reference/sandbox-environment.md), and
[database](docs/reference/database.md).

## Evaluation contracts

### Metrics and judges

- Preserve `fleet_metric`'s five positional arguments and
  `dspy.Prediction(score, feedback, objective_scores)` result.
- Separate serving, judging, and reflection roles; use their model-format helpers.
  Other MLflow scorers do not automatically affect GEPA.
- Preserve route-judge backend metadata, `Noul` at `JEV_ROUTE_THRESHOLD`, route
  `Choice`, and evidence-derived rationale. Keep sandbox success separate, allow
  task-justified calls, and treat evaluated content as data.
- Bump `fleet.jev.rubric_version` when changing the rubric.
- Preserve `execution-evidence-v3`'s JSON-object string schema for judge
  compatibility, with Python validation of booleans, rationales, and `span_id=`
  citations.

### Data and promotion

- Use approved immutable replays grouped by source lineage in isolated runtimes.
  Keep gold/structured expectations outside inputs and holdout outside search.
- Exclude benchmark traces, including `fleet.benchmark.*`-tagged traces, from
  curation. Score artifacts and delegation from host receipts.
- Preserve paired validation, critical holdout, correctness, and measured-usage
  gates. Separate candidate creation from installation.
- Approval must recheck artifact, incumbent, configuration, code, and dependency
  identities. Invalidate incompatible evidence; allow no unchecked install or
  rollback.

### Worker lifecycle

- Keep optimizer stages and counters worker-owned in CLI and managed runs. The
  API drains output and arbitrates managed terminal outcomes after process exit.
- Required persistence failures must stop native GEPA and block publication.
  Optional telemetry must not set that failure latch.
- Preserve both database and file cancellation checks.

References: [evaluation](docs/reference/evaluation.md) and the
[canary runbook](docs/runbooks/evaluation-canary.md).

## Validate and hand off

Use the [Makefile](Makefile) for supported commands. Ordinary CI must remain
independent of live services.

| Change | Required checks |
| --- | --- |
| Code | Add regression coverage, run `uv run pytest <focused-test>`, then `make check`. |
| HTTP API | Run the code checks, update docs, and regenerate/check the OpenAPI contract with `make api-sync` and `make api-check`. |
| Documentation only | Run `make check-docs` and `git diff --check`. |

Report what changed, which checks ran, and anything that could not be completed.
Distinguish offline tests, live canaries, and measured gains. Never present offline
validation as evidence of live compatibility or measured improvement.
