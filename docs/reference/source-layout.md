# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config/            # FleetSettings, TOML/.env/env source tracking, and LLM role builders
│   ├── __init__.py    # Public config exports (FleetSettings, Role, validators)
│   ├── settings.py    # Frozen Pydantic settings model, session overrides, and digest
│   ├── sources.py     # TOML schema validation and configuration provenance helpers
│   └── llm.py         # Role model resolution and runtime parameter builders
├── contracts.py       # Route vocabulary, stopping rules, runtime input names, decision evidence
├── models.py          # Direct native DSPy model configuration and endpoint browsing
├── observability.py   # Redaction, native usage receipts, callbacks, MLflow instrumentation
├── execution_evidence.py # Gold schema plus bounded host-collected artifact/child evidence
├── replay.py          # Encoding and integrity checks for bounded immutable replay snapshots
├── skills.py          # Bundled/user Skill validation, sync, and catalog
├── bundled_skills/    # Packaged default Skills (delegation, long-context-search, repository-inspection, skill-creator, tabular-analysis)
├── doctor.py          # Read-only diagnostics (`fleet doctor`) and snapshot CLI subcommands
├── sandbox/
│   ├── manager.py     # Daytona root/child sandboxes, lifecycle, and workspace coordination
│   ├── budget.py      # Thread-safe Turn budget, depth/capacity, and deadline accounting
│   ├── lifecycle.py   # Sandbox creation, cleanup confirmation, and reconciliation helpers
│   ├── workspace_io.py # Session workspace files, context datasets, and learned notes mixin
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── delegation.py  # Explicit child execution, bounded fan-out, copied policy snapshots
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   ├── snapshots.py   # Packaged definition, runtime preflight/cache, explicit operator operations
│   ├── snapshot-requirements.txt # Packaged hash-locked Python environment
│   └── runtime/
│       └── repl.py    # Sandbox-side namespace, workspace ops, output capture, and bridge
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Static native execution and judge declarations
│   ├── programs.py    # FleetProgram composition and complete native state contract
│   ├── decisions.py   # Routing and optional Skill selection modules/predictors
│   ├── reasoning.py   # Native Direct/RLM/Flex and route input/output projection
│   ├── invocation.py  # Shared host construction, admission, invocation and evidence
│   ├── compiled.py    # Strict fleet-program-b2 envelope and bounded atomic state loading
│   ├── turn.py        # One Turn end to end: orchestrates claim, prepare, run, commit, report
│   ├── turn_prepare.py # Workspace sync, dataset validation, snapshot preflight, replay capture
│   ├── turn_commit.py # Outcome assembly, Store transaction, learned notes, MLflow tracing
│   └── children.py    # ChildPolicies: two persistent native action predictors
├── store/             # Squashed PostgreSQL schema, pooling, leases, and transactions
│   ├── __init__.py    # Store class combining mixins, connection pool, and migrations
│   ├── schema.py      # Version-1 DDL tables and retry policy
│   ├── sessions.py    # Session rows, turns, memories, settings, CAS sandbox resource tracking
│   ├── replays.py     # Content-addressed replay blobs, snapshots, and review labels
│   ├── leases.py      # Pooled TTL leases, heartbeat renewal, and Lakebase OAuth connections
│   └── optimization.py # Global optimization run ledger and owner arbitration
├── optimize/          # GEPA/evaluation modules; never imported by engine/runtime
│   ├── objectives.py  # Pure scoring objectives: no MLflow, LM, or filesystem
│   ├── artifacts.py   # Identity, manifests, reports, promotion gate, approval
│   ├── validation.py  # Isolated replay engines, paired validation, trace evidence
│   ├── dataset.py     # Reviewed examples, grouped splits, canonical dataset identity
│   ├── scorers.py     # MLflow scorer sinks, judge suite, and the GEPA metric
│   ├── jobs.py        # Evaluate-job split summaries, headlines, and installed program identity
│   ├── progress.py    # Worker progress reporting, token cap tracking, and cancellation checks
│   ├── runner.py      # Two-stage GEPA + ReAnchor runner and paired validation orchestration
│   ├── cli_commands.py # Synchronous CLI optimizer/check/approve execution helpers
│   ├── gepa.py        # Public `fleet optimize` optimization facade
│   └── evaluation.py  # Eval cases, synthetic generators, gold labels, `fleet eval` runs
├── optimization_runs.py # CLI/API-neutral options, events, import, and artifact reads
├── experiments.py     # API-owned job process (ExperimentManager) and lifecycle
├── api/
│   ├── app.py         # Single-mode create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and nested SSE envelope
│   ├── stream_contracts.py # Version-1 JSON envelope validation inside FastAPI SSE frames
│   └── routes/        # sessions (+ memory, skills), workspace, turns, improve
├── session.py         # Runtime, registry, leases, memory/history/Turn-record policies
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli` and `fleet web`
├── tui/
│   ├── app.py         # Fullscreen Textual application and non-TTY fallback loop
│   ├── widgets.py     # Prompt composer, command menu, collapsible steps, and Turn widget
│   ├── stream.py      # Turn SSE request and event stream parser
│   ├── commands.py    # Slash-command table (dispatch, /help, completion)
│   ├── improve.py     # Improve screen: label Turns, check readiness, run/review global GEPA jobs
│   ├── local_paths.py # Git-aware local path discovery for mention search and directory uploads
│   ├── mentions.py    # Local and cached workspace @mentions for the Textual composer
│   ├── render.py      # Event rendering
│   ├── web.py         # Textual Serve launcher and safely quoted client command
│   └── web_client.py  # Per-browser Textual app process
└── cli.py             # `fleet` entry point: serve, cli, web, doctor, snapshot, optimize, eval, trace
```

The FastAPI app (`api/app.py`) is the primary application: a Turn enters through
`api/routes/turns.py`, runs on a worker thread, and ends with one terminal outcome.
`/improve` job routes delegate GEPA/evaluate execution to a CLI worker subprocess
managed by `experiments.py`: the engine/runtime never imports `fleet_rlm.optimize`
and the API never imports GEPA, while `/improve` review/status routes lazily read
`optimize.dataset` and `optimize.evaluation`.
PostgreSQL stores API-owned and standalone CLI run lifecycle. `fleet optimize`
remains an independently callable synchronous CLI workflow. `optimization_runs.py`
is neutral to the serving boundary and holds shared options, worker events, and
artifact reads without rewriting the source files.

Release checks live in repository tooling at `scripts/release_checks.py`, outside
the wheel. Module boundaries follow learned composition versus host authority;
they do not add a new reasoning loop or lifecycle owner.

### Python import changes

- `fleet_rlm.engine.programs.AdaptiveFleetEngine` is removed, without an alias.
  Use `engine.invocation.FleetRuntime` for host execution and its `.program`
  (`engine.programs.FleetProgram`) for native DSPy state/traversal.
- `fleet_rlm.engine.programs.CompiledProgramFile` → `fleet_rlm.engine.compiled.CompiledProgramFile`.
- `fleet_rlm.lm_usage` → `fleet_rlm.observability`.
- `fleet_rlm.store.db` → `fleet_rlm.store`.
- `fleet_rlm.config` package (`settings`, `sources`, `llm`) replaces single-module `config.py`, while `fleet_rlm.config.FleetSettings` remains the public import.
- Snapshot definition, preflight, and operations now share `fleet_rlm.sandbox.snapshots`.
- Synthetic generators moved from `fleet_rlm.optimize.benchmarks` to `fleet_rlm.optimize.evaluation`.
- Isolated engine construction and `IsolatedFleetProgram` live in `optimize.validation`;
  identity/report helpers live in `optimize.artifacts`; `dataset_digest` lives in `optimize.dataset`.
- Saved mode/root/sub overrides use `FleetSettings.for_session`; the former harness
  methods live directly on `SessionRuntime`, with `runtime.store` for persistence.

Old submodule paths have no compatibility shims. CLI/HTTP operations remain stable;
Gate A intentionally changes compiled format and native parameter paths. The
isolated wrapper still prefixes its internal state with `program.`, but installation
accepts only an explicitly exported unwrapped root envelope, not arbitrary prefixes.
`engine/signatures.py` remains a declaration boundary; GEPA traverses the instantiated
student, not declaration files. `contracts.py` stays a leaf. Optimizer dependencies
flow from `gepa` to dataset/validation/artifacts/scorers, never back into orchestration.

## Test layout

`tests/` groups offline regression cases into domain suites, with descriptive
sections inside each module:

| Area | Suites |
| --- | --- |
| HTTP API | `api/test_turns.py` (routes, lifecycle, outcomes, SSE, managed experiments) |
| Runtime | `engine/test_runtime.py` (native programs, configuration, models, Sessions, Skills, usage, tracing) |
| Sandbox | `sandbox/test_sandbox.py` (lifecycle, budgets, snapshots, interpreter, delegation, transport, workspace) |
| Optimization | `optimize/test_optimization.py` (workers, artifacts, native GEPA, isolated rollouts, promotion) |
| Evaluation | `optimize/test_evaluation.py` (curation, benchmarks, reports, scorers, objectives, evidence) |
| Clients | `tui/test_clients.py` (CLI, launcher, Textual UI, browser security) |
| Persistence | `store/test_store.py` (migrations, history, leases, opt-in database contracts) |
| CI and publication | `test_release_checks.py` (discovery, release automation, publication evidence, verifiers) |

The consolidation retains behavioral regressions while removing six legacy dynamic-schema
comparison cases in favor of native field/type and state round-trip checks. Two
redundant route vocabulary/instruction checks are covered by typed triage criteria
and route-selection cases. The removed timeout source-substring check is covered
by explicit unbounded (`None`/zero) and bounded execution tests. Live database
opt-ins remain separate from ordinary offline validation.

Select a domain with `uv run pytest tests/<area>/<suite>.py`, and narrow to a
contract with `-k`. Configuration precedence, per-file child-transfer fakes, and
rollout provider refusal use explicit fixture marks so their setup does not leak
into unrelated cases in the same suite. API fakes and the sandbox emulator live in their consuming suites. Recorded JSON
fixtures share `tests/fixtures.json`; the root conftest owns shared environment isolation. Live
database tests retain explicit environment opt-ins and skip in ordinary CI.

Offline tests persist native MLflow traces synchronously to their disposable SQLite
store and restore DSPy callbacks after each case, so enabling autologging in one
test does not add trace writes to unrelated cases. Production tracing settings are
unchanged. Use `uv run pytest --durations=30` to inspect slow cases; deadline
admission tests advance a local clock instead of waiting for public timeout bounds.
The native Flex constructor matrix shares one guest program and one reload while
checking every syntax/default variant, avoiding repeated guest interpreter startup.

Repository commands are defined in the root `Makefile`; CI discovers `test_*.py`
files dynamically and runs those same quality targets, including Store migration
regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
