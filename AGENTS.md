# Repository guidelines

Fleet RLM Lite uses Textual, FastAPI/SSE, DSPy, Daytona, PostgreSQL, and MLflow/GEPA.
Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries; consult
[source layout](docs/reference/source-layout.md), current source, `pyproject.toml`, and `uv.lock`.

## Working conventions

- Start with `git status --short` and the existing diff; preserve uncommitted work, including deletions.
- Use Python 3.11–3.13 with `uv`, four-space indentation, and Ruff; prefer `pnpm` for JavaScript.
- Keep changes scoped. Reuse existing abstractions and native DSPy behavior; avoid speculative abstractions and compatibility aliases.
- Before removing code, check callers, persisted formats, migrations, replay compatibility, tests, and docs.
- User Skills live in `.fleet/skills/`; never synchronize developer guidance from `.agents/skills/` as user Skills.
- Update docs for public behavior changes. Keep external benchmarks/verifiers in notebooks/helpers and runtime policy independent of benchmark families.
- Never commit credentials or `.env` files; treat traces/artifacts as sensitive. Commit, push, publish, and deploy only with explicit authorization; use conventional commit prefixes.
- Live Daytona, model-provider, database, and evaluation runs require explicit authorization. Preserve run/trace IDs and recover completed work before rerunning; GEPA call budgets do not cap total cost.

## Runtime guardrails

- Preserve `direct` / `flex` / `rlm`, strict-mode triage bypass, dataset routing constraints, and native DSPy tuning/delegation behavior.
- Execute generated Python in Daytona; keep credentials and host-mediated tools on the server. Preserve shared Turn budgets, isolated children, success-only artifact promotion, and provenance.
- Preserve one terminal outcome and teardown owner per Turn. On failure, cancellation, expiry, or disconnect before an outcome, fence host work, shut down children, and stop the root before acknowledgment. Success retains the warm root.
- Preserve execution reply fences and generation-bound watchdogs serialized with Turn end/reset; fencing alone does not interrupt providers or stop children.
- Keep the 900-second default; request overrides configuration, and exactly `0` disables only Fleet's deadline. `/timeout` is client-local; child deadlines cannot exceed the parent's.
- Progress is advisory; synchronize ingestion/SSE sampling, discard pending progress after an outcome, and keep `done` last.
- Preserve snapshot resource floors and provider-resolved provenance, historical `pre-provenance` readability, Store migrations, and runtime lease fencing. Stored history cannot restore live interpreter state.
- Keep ordinary API access loopback by default, explicit non-loopback opt-in, and a secured boundary for external exposure.
- Use scoped `dspy.context`; preserve native tracing, evaluation, and serialization, and validate pinned integration contracts on upgrades.

Details: [HTTP API](docs/reference/http-api.md), [configuration](docs/reference/configuration.md),
[sandbox](docs/reference/sandbox-environment.md), and [database](docs/reference/database.md).

## Evaluation guardrails

- Preserve `fleet_metric`'s five positional arguments and `dspy.Prediction(score, feedback, objective_scores)` result. Separate serving, judging, and reflection roles; use their model-format helpers. Other MLflow scorers do not automatically affect GEPA.
- Preserve route-judge backend metadata, `Noul` at `JEV_ROUTE_THRESHOLD`, route `Choice`, and evidence-derived rationale. Keep sandbox success separate, allow task-justified calls, treat evaluated content as data, and bump `fleet.jev.rubric_version` for rubric changes.
- Use approved immutable replays grouped by source lineage in isolated runtimes. Keep gold/structured expectations outside inputs, holdout outside search, and benchmark traces outside curation; score artifacts/delegation from host receipts.
- Preserve paired validation, critical holdout, correctness, and measured-usage gates. Separate candidate creation from installation; approval must recheck artifact, incumbent, configuration, code, and dependency identities. Invalidate incompatible evidence; allow no unchecked install or rollback.
- Keep optimizer stages/counters worker-owned in both CLI and managed runs; the API drains output and arbitrates managed terminal outcomes after process exit. Required persistence failures must stop native GEPA and block publication; optional telemetry must not set that failure latch. Preserve both database and file cancellation checks.
- Distinguish offline tests, live canaries, and measured gains. See [evaluation](docs/reference/evaluation.md) and the [canary runbook](docs/runbooks/evaluation-canary.md).

## Validation

Use the [Makefile](Makefile) for supported commands:

- Code: add regression coverage, run `uv run pytest <focused-test>`, then `make check`.
- Documentation only: run `make check-docs` and `git diff --check`.
- API changes: update docs and regenerate/check the OpenAPI contract with existing tooling.
- Ordinary CI must remain independent of live services.
