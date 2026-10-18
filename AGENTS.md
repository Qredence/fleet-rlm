# Repository guidelines

Fleet RLM Lite is the active Python application: a Rich / Prompt Toolkit client,
FastAPI SSE server, DSPy engine, Daytona execution, PostgreSQL persistence, and
MLflow evaluation with native DSPy GEPA optimization.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime boundaries.
Use the current source, `pyproject.toml`, and `uv.lock` as the implementation and
dependency references. Do not describe removed APIs, clients, migrations, or
configuration features as current behavior.

## Ownership and layout

| Location | Responsibility |
| --- | --- |
| `src/fleet_rlm/cli.py`, `tui/` | CLI entry point and streaming terminal client |
| `src/fleet_rlm/api/` | FastAPI routes, request/response schemas, SSE events |
| `src/fleet_rlm/config.py`, `session.py` | Typed settings and Session runtime lifecycle |
| `src/fleet_rlm/engine/` | Turn orchestration, signatures, routing, RLM/Flex, delegation |
| `src/fleet_rlm/sandbox/` | Daytona lifecycle, interpreter, execution transport, sandbox runtime |
| `src/fleet_rlm/store/`, `replay.py` | Persistence, migrations, immutable replay capture and review |
| `src/fleet_rlm/optimize/` | Datasets, metrics, execution evidence, GEPA, validation and program artifacts |
| `src/fleet_rlm/skills.py`, `bundled_skills/` | Skill validation, synchronization, bundled workflows |
| `tests/`, `docs/`, `scripts/` | Tests, maintained documentation, development and validation tooling |

Paths within a source row are relative to `src/fleet_rlm/` unless fully specified.
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
  tools on the server. Preserve shared Turn budgets, deadlines, child isolation,
  cleanup, and artifact provenance.
- Persist durable session data, not assumptions about live interpreter state.
  Use the existing Store/migration mechanisms for schema changes.
- Use scoped `dspy.context` overrides for model/adapter settings. Preserve native
  DSPy trace, evaluation, and serialization contracts; validate compatibility
  before changing dependency versions or internal DSPy imports.

## Evaluation and GEPA invariants

- Keep `fleet_metric` as the shared GEPA/evaluation metric. Preserve its five
  positional arguments and `dspy.Prediction(score, feedback, objective_scores)`
  result. Other MLflow scorers do not automatically influence GEPA search.
- Keep serving, judging, and reflection roles explicit. Model identifiers for
  MLflow judges and DSPy LMs are different formats; use the configuration helpers.
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

- Preserve existing uncommitted work. Keep edits scoped to the requested task.
- Use four-space Python indentation and Ruff formatting/linting. Prefer existing
  abstractions and native DSPy functionality over duplicate framework logic.
- Update documentation when public behavior changes. Describe measured results
  separately from intended behavior and unverified performance claims.
- Never commit credentials or `.env` files. Treat traces and artifacts as
  potentially sensitive even when redaction is enabled.
- Use conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  or `chore:`. Do not commit, push, publish, or deploy without explicit authorization.
