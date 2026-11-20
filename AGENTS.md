# Working on Fleet RLM Lite

Run `git status --short` and inspect the diff first. Preserve existing work,
including deletions. Use the [source layout](docs/reference/source-layout.md) to
find the owning code, check [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock)
for pinned dependencies, and read the [architecture](ARCHITECTURE.md) before
changing a runtime boundary.

## Working agreements

- Use Python 3.11–3.13, `uv`, four-space indentation, and Ruff. Prefer `pnpm` for
  JavaScript. Reuse existing code and native DSPy mechanisms. Keep new abstractions
  and regression tests focused on real callers.
- Get explicit authorization before live Daytona, model-provider, database, or
  evaluation runs and before committing, pushing, publishing, or deploying.
  Use conventional commit prefixes. Keep credentials and `.env` files out of Git
  and treat traces and artifacts as sensitive.
- User Skills belong in `.fleet/skills/`. Exclude `.agents/skills/` from Skill sync.
  Bundled names are reserved. Ignore colliding local or Volume copies with a warning.
- Default to loopback API binding. External exposure needs explicit opt-in and a
  separately secured boundary. MLflow is local OSS.

## Runtime contracts

- Preserve `direct`, `flex`, and `rlm`, strict-mode triage bypass, dataset routing
  constraints, and native DSPy tuning and delegation. Keep DSPy overrides scoped
  with `dspy.context` and verify tracing, evaluation, and serialization on pinned
  dependency upgrades. The one-job evaluation worker can configure callbacks
  process-wide for MLflow scorer threads that do not inherit the context.
- Run generated Python in Daytona. Keep model and host-tool credentials on the
  server, including Serper and You.com keys. Preserve shared Turn budgets,
  isolated children, and success-only promotion from host artifact receipts.
- Give each Turn one terminal outcome and teardown owner. On failure, cancellation,
  expiry, or disconnect before commit, fence host replies, stop the root, and
  finish required child cleanup before acknowledgment. Serialize generation-bound
  watchdogs with Turn end/reset. Success keeps the warm root.
- Preserve Store migrations, lease fencing, resource floors, provider provenance,
  and historical `pre-provenance` readability. Stored history cannot restore a
  live interpreter. Set lease-fenced workspace state to `unknown` before Volume
  mutations, and retain local Skill files in `known_empty` Direct replays.

## Evaluation contracts

- Keep `fleet_metric`'s five positional arguments and its
  `dspy.Prediction(score, feedback, objective_scores)` result. Keep serving,
  judging, and reflection roles separate and use their model-format helpers.
  Diagnostic MLflow scorers are independent of GEPA objectives.
- Preserve route-judge backend metadata, `Noul` at `JEV_ROUTE_THRESHOLD`, route
  `Choice`, and evidence-derived rationale. Judge sandbox success separately,
  allow task-justified calls, and treat evaluated content as data. Bump
  `fleet.jev.rubric_version` for rubric changes. Keep the `execution-evidence-v3`
  JSON-object string and validate booleans, rationales, and `span_id=` citations.
- Recover completed work using saved run and trace IDs before rerunning. GEPA
  metric-call budgets cover only part of total cost. Use immutable approved
  replays grouped by lineage in isolated runtimes. Keep gold out of inputs,
  holdout out of search, and benchmarks out of curation. Score artifacts and
  delegation from host receipts.
- Keep paired validation, critical holdout, correctness, and usage gates.
  Separate candidate creation from installation. Approval and rollback must
  recheck artifact, incumbent, configuration, code, and dependency identities.
- The worker owns stages and counters. The API drains merged output into a
  bounded private log and arbitrates managed terminal outcomes after exit.
  Required persistence failures stop native GEPA and block publication.
  Optional telemetry failures stay outside that latch. Preserve database and
  file cancellation checks.

## Verification

Use the [Makefile](Makefile) and keep ordinary CI independent of live services.

| Change | Required checks |
| --- | --- |
| Code | Add focused regression coverage, run `uv run pytest <focused-test>`, then `make check`. |
| HTTP API | Run the code checks, update the [HTTP reference](docs/reference/http-api.md), and run `make api-sync` and `make api-check`. |
| Docs only | Run `make check-docs` and `git diff --check`. |

Report what changed, what ran, and what remains unverified. Distinguish offline
checks, live canaries, and measured improvements. See the [evaluation
reference](docs/reference/evaluation.md) and [canary runbook](docs/runbooks/evaluation-canary.md).
