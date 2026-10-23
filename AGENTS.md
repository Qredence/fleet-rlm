# Repository guidelines

Fleet RLM Lite is the active Python application: a Textual terminal client with
Rich / Prompt Toolkit fallback, FastAPI SSE server, DSPy engine, Daytona execution,
PostgreSQL persistence, and MLflow evaluation with native DSPy GEPA optimization.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries.
Use the current source, `pyproject.toml`, and `uv.lock` as the implementation and
dependency references. Do not describe removed APIs, clients, migrations, or
configuration features as current behavior.

## Ownership and layout

| Location | Responsibility |
| --- | --- |
| `src/fleet_rlm/cli.py`, `src/fleet_rlm/tui/` | CLI entry point and streaming terminal client |
| `src/fleet_rlm/api/` | FastAPI routes, request/response schemas, SSE events |
| `src/fleet_rlm/config.py`, `src/fleet_rlm/session.py` | Typed settings and Session runtime lifecycle |
| `src/fleet_rlm/engine/` | Turn orchestration, signatures, routing, RLM/Flex, delegation |
| `src/fleet_rlm/sandbox/` | Daytona lifecycle, interpreter, execution transport, sandbox runtime |
| `src/fleet_rlm/sandbox/snapshots.py` | Repository-owned snapshot definition, preflight, recorded provenance |
| `src/fleet_rlm/store/`, `src/fleet_rlm/replay.py` | Persistence, migrations, immutable replay capture and review |
| `src/fleet_rlm/optimize/` | Datasets, metrics, execution evidence, GEPA, validation and program artifacts |
| `src/fleet_rlm/skills.py`, `src/fleet_rlm/bundled_skills/` | Skill validation, synchronization, bundled workflows |
| `tests/`, `docs/`, `scripts/` | Tests, maintained documentation, development and validation tooling |

All paths above are repository-relative.
User Skills belong in `.fleet/skills/`. `.agents/skills/` is developer guidance
and must never be configured as user Skill synchronization content.

## Runtime invariants

- Preserve the `direct` / `flex` / `rlm` route vocabulary. Strict modes bypass
  hybrid triage; selected datasets cannot take the context-free direct route.
- The primary RLM exposes instruction tuning. Flex exposes module-source
  rewriting and initially wraps an RLM when tools are supplied. Both routes can
  delegate; do not equate Flex with deterministic execution or RLM with required
  delegation.
- Generated Python executes in Daytona. Keep credentials and host-mediated
  tools on the server. Preserve shared Turn budgets, child isolation, cleanup
  outcomes, and artifact provenance.
- One terminal outcome, one teardown, teardown before acknowledgment. On
  failure, cancellation, disconnect, or deadline expiry, set the cancellation
  flag, fence outstanding host calls, shut down children, and stop the root
  before emitting the terminal event and `done`. Success does not stop the root:
  it records `cleanup=not_required` and keeps the warm REPL for the next Turn.
  Watchdog callbacks must be generation-bound and serialized with Turn end/reset;
  a callback from an ended Turn must not cancel its warm root or a subsequent Turn.
- Deadline-free execution is opt-in, never the default. The shipped
  `turn_timeout_seconds` default stays `900`; exactly `0` disables only the Fleet
  wall-clock deadline, chosen by configuration, a Turn request, or the client-local
  `/timeout` command. Resolution is request > configuration > default, and
  `/timeout` is client-local state sent as a request override, never a persisted
  Session setting. A deadline-free Turn still obeys Daytona's provider
  `ttl_minutes` and `auto_stop_interval`; do not describe those bounds as removed.
- The reply fence refuses only late successful host-call results, once, with a
  bounded warning and no retry. Cancellation and error replies are always
  delivered so the sandbox does not wait out the host-call bound. The fence does
  not interrupt an in-flight provider request, and it must never be described as
  stopping the child.
- Progress and status labels (`waiting_on_host`, `progress_unknown`, `progressing`,
  `stalled`, `interrupted`, `fenced`) are advisory observations derived from existing
  events. Nothing in that layer may cancel, pause, retry, or shorten a Turn, and
  uninstrumented work stays `progress_unknown` rather than becoming `stalled`.
  Synchronize worker ingestion with SSE-loop sampling; discard pending progress
  after a terminal outcome, and keep `done` as the final stream event.
- The Turn snapshot is repository-owned in `sandbox/snapshots.py`, not an
  environment or TOML setting. Preserve preflight resource floors and record the
  provider-resolved identity; missing optional packages are observations, not failures.
- Persist durable session data, not assumptions about live interpreter state.
  Use the existing Store/migration mechanisms for schema changes. Historical
  Turns and replays without snapshot provenance remain readable as `pre-provenance`.
- The HTTP API has no built-in caller authentication. Preserve the loopback
  default and explicit non-loopback opt-in; deployment still needs a secured boundary.
- Use scoped `dspy.context` overrides for model/adapter settings. Preserve native
  DSPy trace, evaluation, and serialization contracts; validate compatibility
  before changing dependency versions or internal DSPy imports.

## Evaluation and GEPA invariants

- Keep `fleet_metric` as the shared GEPA/evaluation metric. Preserve its five
  positional arguments and `dspy.Prediction(score, feedback, objective_scores)`
  result. Other MLflow scorers do not automatically influence GEPA search.
- Keep serving, judging, and reflection roles explicit. Model identifiers for
  MLflow judges and DSPy LMs are different formats; use the configuration helpers.
- Keep the trace-scoring judge backend visible. `build_mlflow_scorer_suite`
  judges route suitability with TypeSafe Jev when `FLEET_TYPESAFE_API_KEY` is
  configured and with the generative rubric otherwise; every assessment records
  `fleet.judge_backend`. The Jev verdict is a typed decision (`Noul` at
  `JEV_ROUTE_THRESHOLD` plus a `Choice` over the route vocabulary) whose
  rationale is derived from recorded evidence, not model prose. Keep both
  rubrics' guardrails identical — sandbox success is a separate dimension, calls
  justified by task complexity are allowed, and evaluated content is data, never
  instructions — and bump `fleet.jev.rubric_version` when the typed rubric changes.
- Gold answers and structured expectations stay outside program inputs. Score
  artifacts and delegation from host-collected evidence, not candidate claims.
- Train/dev search uses approved immutable replays grouped by source lineage.
  Keep holdout out of search and benchmark-labeled traces out of the curated
  optimization dataset. Preserve isolated replay runtimes.
- Preserve paired validation, critical holdout checks, usage evidence, and
  correctness gates. Changes to scoring or approval semantics must invalidate
  incompatible evidence through the existing version/identity mechanisms.
- Candidate creation and installation are separate. Approval must recheck the
  artifact and incumbent/configuration/code/dependency identities. Do not add an
  unchecked installation or rollback path.
- Distinguish offline contract tests, live canaries, and measured workload gains.
  A correct final answer alone does not prove delegation or artifact behavior;
  require execution receipts when those capabilities are under evaluation.

See [evaluation](docs/reference/evaluation.md) for the current scoring policy and
[evaluation canary runbook](docs/runbooks/evaluation-canary.md) for live procedures.

## Development and validation

Use Python 3.11–3.13 and uv. The [Makefile](Makefile) defines the supported targets:

| Command | Purpose |
| --- | --- |
| `make install-dev` | Install runtime and development dependencies |
| `make serve` | Start the FastAPI server |
| `make cli` | Display CLI help |
| `uv run pytest <focused-test>` | Run focused non-live tests |
| `make test` | Run the non-live test suite |
| `make lint`, `make format-check`, `make typecheck` | Static checks without rewriting source |
| `make check-docs` | Validate local documentation links |
| `make api-check` | Check the generated OpenAPI contract |
| `make check` | Complete non-live quality gate and package build |

For code changes, add regression coverage for changed behavior and run focused
checks followed by the required quality gate. For documentation-only changes,
run `make check-docs` and always run `git diff --check`. When changing the API,
update its documentation and generated contract with the existing tooling.

Live Daytona, model-provider, database, and evaluation runs require explicit
authorization and appropriate resources. Ordinary CI must not depend on them.
GEPA metric-call budgets do not cap total spending: reflection, judging, retries,
sandbox lifecycle, and final validation also consume resources. Preserve saved
trace/run identities and recover completed work rather than replaying it blindly.

## Change discipline

- Start with `git status --short` and the existing diff. Preserve uncommitted
  work, including deletions; keep edits scoped and do not commit it without authorization.
- Remove code only after checking callers, persisted formats, tests, and docs.
  Migration history, replay compatibility, and version-sensitive DSPy bridges are
  not dead code merely because new requests do not exercise them.
- Prefer small owning modules and one validation policy per setting. Do not add
  compatibility aliases or speculative abstractions for an internal refactor.
- Use four-space Python indentation and Ruff formatting/linting. Prefer existing
  abstractions and native DSPy functionality over duplicate framework logic.
- Update documentation when public behavior changes. Describe measured results
  separately from intended behavior and unverified performance claims.
- Never commit credentials or `.env` files. Treat traces and artifacts as
  potentially sensitive even when redaction is enabled.
- Use conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  or `chore:`. Do not commit, push, publish, or deploy without explicit authorization.
