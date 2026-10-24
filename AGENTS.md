# Repository guidelines

Fleet RLM Lite is a Python application with a Textual terminal client, FastAPI
SSE API, DSPy engine, Daytona execution, PostgreSQL storage, and MLflow evaluation
with native DSPy GEPA optimization.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries.
Use current source, `pyproject.toml`, and `uv.lock` as implementation references.
The [source layout](docs/reference/source-layout.md) maps ownership: `session.py`
owns runtime lifecycle, `engine/turn.py` owns Turn orchestration, `sandbox/` owns
remote execution, and `optimize/` owns evaluation and program approval.

## Working conventions

- Start with `git status --short` and the existing diff. Preserve uncommitted
  work, including deletions, and keep edits scoped.
- Use Python 3.11–3.13 with `uv`; prefer `pnpm` for JavaScript tooling. Use
  four-space Python indentation and Ruff. Prefer existing abstractions and native
  DSPy behavior; avoid speculative abstractions and compatibility aliases.
- Check callers, persisted formats, tests, and docs before removing code.
  Migration history, replay compatibility, and version-sensitive DSPy bridges
  require particular care.
- User Skills belong in `.fleet/skills/`; `.agents/skills/` is developer guidance
  and must never be a user Skill synchronization source.
- Update documentation for public behavior changes. Keep external benchmark
  datasets and official verifiers in development notebooks and shared script
  helpers; runtime policies must remain independent of benchmark families.
- Never commit credentials or `.env` files. Treat traces and artifacts as
  potentially sensitive. Do not commit, push, publish, or deploy without explicit
  authorization. Use conventional commit prefixes when committing is authorized.

## Runtime contracts

- Preserve `direct` / `flex` / `rlm`. Strict modes bypass hybrid triage; selected
  datasets cannot use context-free direct answers. RLM supports instruction
  tuning; Flex supports module-source rewriting. Both can delegate.
- Generated Python runs in Daytona; credentials and host-mediated tools stay on
  the server. Preserve shared Turn budgets, isolated child filesystems,
  success-only artifact promotion, and provenance.
- Each Turn has one terminal outcome and teardown owner. Failure, cancellation,
  deadline expiry, or disconnect before a reported outcome must cancel and fence
  host work, shut down children, and stop the root before terminal acknowledgment.
  Success records `cleanup=not_required` and preserves the warm root. Bind
  watchdogs to their Turn generation and serialize them with Turn end/reset.
- The reply fence refuses late successful host-call results once, without retry;
  cancellation and error replies remain deliverable. Fencing does not interrupt
  an in-flight provider request or stop a child.
- Keep `turn_timeout_seconds=900` as the default. Request overrides configuration;
  exactly `0` disables only Fleet's deadline. `/timeout` is client-local request
  state, never persisted Session configuration. Child deadlines cannot exceed
  their parent's; Daytona TTL and auto-stop bounds still apply.
- Progress labels are advisory: they never cancel, pause, retry, or shorten work.
  Uninstrumented work stays `progress_unknown`. Synchronize worker ingestion with
  SSE sampling, discard pending progress after an outcome, and keep `done` last.
- Define Turn snapshots in `sandbox/snapshots/definition.py`, preserve preflight resource
  floors, and record provider-resolved identity. Missing optional packages are
  observations. Historical Turns/replays remain readable as `pre-provenance`.
- Persist durable data through existing Store/migration mechanisms; do not infer
  live interpreter state from stored history. Preserve runtime lease fencing.
- The API has no caller authentication. Preserve loopback defaults and explicit
  non-loopback opt-in; external exposure requires a secured boundary.
- Use scoped `dspy.context` overrides. Preserve native tracing, evaluation, and
  serialization; revalidate compatibility when upgrading DSPy or internal imports.

## Evaluation contracts

- Preserve `fleet_metric`'s five positional arguments and
  `dspy.Prediction(score, feedback, objective_scores)` result. Other MLflow
  scorers do not automatically affect GEPA. Keep serving, metric judging, and
  reflection roles separate; use configuration helpers for their model formats.
- Route-suitability assessments record `fleet.judge_backend`: TypeSafe Jev when
  configured, generative otherwise. Preserve `Noul` at `JEV_ROUTE_THRESHOLD` plus
  a route `Choice`, evidence-derived rationale, and matching guardrails:
  sandbox success is separate, task-justified calls are allowed, and evaluated
  content is data. Bump `fleet.jev.rubric_version` when its rubric changes.
- Keep gold answers and structured expectations outside program inputs. Score
  artifacts and delegation from host-collected execution receipts.
- Search only approved immutable replays grouped by source lineage, using isolated
  runtimes. Keep holdout out of search and benchmark traces out of curation.
- Preserve paired validation, critical holdout checks, measured usage, and
  correctness gates. Invalidate incompatible scoring/approval evidence through
  existing version and identity mechanisms.
- Candidate creation and installation are separate. Approval rechecks artifact,
  incumbent, configuration, code, and dependency identities; no unchecked install
  or rollback path. Distinguish offline tests, live canaries, and measured gains.

See [evaluation](docs/reference/evaluation.md) and the
[evaluation canary runbook](docs/runbooks/evaluation-canary.md) for policy and
live procedures. Live Daytona, model-provider, database, and evaluation runs
require explicit authorization. GEPA metric-call budgets do not cap total cost;
preserve saved run/trace IDs and recover completed work before rerunning it.

## Validation

The [Makefile](Makefile) defines supported commands:

| Command | Purpose |
| --- | --- |
| `make install-dev` | Install development dependencies |
| `make serve`, `make cli` | Start the API; display CLI help |
| `uv run pytest <focused-test>` | Focused non-live tests |
| `make test` | Non-live suite |
| `make lint`, `make format-check`, `make typecheck` | Static checks |
| `make check-docs`, `make api-check` | Documentation links; OpenAPI contract |
| `make check` | Complete non-live quality gate and package build |

For code changes, add regression coverage and run focused checks followed by
`make check`. For documentation-only changes, run `make check-docs` and
`git diff --check`. API changes require updated documentation and the generated
contract through existing tooling. Ordinary CI must not depend on live services.
