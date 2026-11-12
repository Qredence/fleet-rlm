# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config.py          # FleetSettings and model construction
├── contracts.py       # Route vocabulary, stopping rules, runtime input names, decision evidence
├── models.py          # Direct native DSPy model configuration and endpoint browsing
├── observability.py   # Redaction, native usage receipts, callbacks, MLflow instrumentation
├── execution_evidence.py # Gold schema plus bounded host-collected artifact/child evidence
├── replay.py          # Encoding and integrity checks for bounded immutable replay snapshots
├── skills.py          # Bundled/user Skill validation, sync, and catalog
├── bundled_skills/    # Packaged default Skills (delegation, long-context-search, repository-inspection, skill-creator, tabular-analysis)
├── sandbox/
│   ├── manager.py     # Daytona root/child sandboxes, lifecycle, and budgets
│   ├── workspace_io.py # Session workspace files, context datasets, and learned notes mixin
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── delegation.py  # Explicit child execution, bounded fan-out, copied policy snapshots
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   ├── snapshots.py   # Packaged definition, runtime preflight/cache, explicit operator operations
│   ├── snapshot-requirements.txt # Packaged hash-locked Python environment
│   └── runtime/
│       ├── repl.py    # Sandbox-side namespace, output capture, and bridge
│       └── fs_ops.py  # Sandbox-side filesystem, staging, digest, notes, and Skill scripts
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Static native execution and judge declarations
│   ├── programs.py    # FleetProgram composition and complete native state contract
│   ├── decisions.py   # Routing and optional Skill selection modules/predictors
│   ├── reasoning.py   # Native Direct/RLM/Flex and route input/output projection
│   ├── invocation.py  # Shared host construction, admission, invocation and evidence
│   ├── compiled.py    # Strict fleet-program-b1 envelope and bounded atomic state loading
│   ├── turn.py        # One Turn end to end: claim, prepare, run, persist, report
│   └── children.py    # ChildPolicies: two persistent native action predictors
├── store.py          # SQL, numbered additive migrations, one pool, Turn transactions
├── optimize/          # GEPA/evaluation modules; never imported by engine/runtime
│   ├── objectives.py  # Pure scoring objectives: no MLflow, LM, or filesystem
│   ├── artifacts.py   # Identity, manifests, reports, promotion gate, approval
│   ├── validation.py  # Isolated replay engines, paired validation, trace evidence
│   ├── dataset.py     # Reviewed examples, grouped splits, canonical dataset identity
│   ├── scorers.py     # MLflow scorer sinks, judge suite, and the GEPA metric
│   ├── jobs.py        # Evaluate-job split summaries, headlines, and installed program identity
│   ├── gepa.py        # `fleet optimize`
│   └── evaluation.py  # Eval cases, synthetic generators, gold labels, `fleet eval` runs
├── optimization_runs.py # CLI/API-neutral options, events, import, and artifact reads
├── experiments.py     # API-owned job process (ExperimentManager) and lifecycle
├── api/
│   ├── app.py         # create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and SSE envelope
│   ├── stream_contracts.py # Version-1 JSON envelope validation inside FastAPI SSE frames
│   └── routes/        # sessions (+ memory, skills), setup, workspace, turns, improve
├── setup.py           # Configuration draft, readiness probes, serialized setup operations
├── session.py         # Runtime, registry, leases, memory/history/Turn-record policies
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli` and `fleet web`
├── tui/
│   ├── app.py         # Fullscreen Textual application and non-TTY fallback loop
│   ├── widgets.py     # Prompt composer, command menu, collapsible steps, and Turn widget
│   ├── stream.py      # Turn SSE request and event stream parser
│   ├── setup.py       # FastAPI-backed startup checklist
│   ├── commands.py    # Slash-command table (dispatch, /help, completion)
│   ├── improve.py     # Improve screen: label Turns, check readiness, run/review global GEPA jobs
│   ├── local_paths.py # Git-aware local path discovery for mention search and directory uploads
│   ├── mentions.py    # Local and cached workspace @mentions for the Textual composer
│   ├── render.py      # Event rendering
│   ├── web.py         # Textual Serve launcher and safely quoted client command
│   └── web_client.py  # Per-browser Textual app process
└── cli.py             # `fleet` entry point: serve, cli, web, optimize, eval
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
- `fleet_rlm.store.db` → `fleet_rlm.store`; `fleet_rlm.setup.service` → `fleet_rlm.setup`.
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

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there, including Store migration regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
