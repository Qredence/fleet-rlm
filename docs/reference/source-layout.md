# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config.py          # FleetSettings and model construction
├── contracts.py       # Route vocabulary, stopping rules, runtime input names, decision evidence
├── adapters.py        # Native ChatAdapter recovery with recorded gateway parse compatibility
├── observability.py   # Redaction, native usage receipts, callbacks, MLflow instrumentation
├── execution_evidence.py # Gold schema plus bounded host-collected artifact/child evidence
├── skills.py          # User Skill validation, sync, and catalog
├── sandbox/
│   ├── manager.py     # Daytona root/child sandboxes, lifecycle, and budgets
│   ├── workspace_io.py # Session workspace files, context datasets, and learned notes mixin
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   ├── snapshots.py   # Packaged definition, runtime preflight/cache, explicit operator operations
│   ├── snapshot-requirements.txt # Packaged hash-locked Python environment
│   └── runtime/
│       ├── repl.py    # Sandbox-side namespace, output capture, and bridge
│       └── fs_ops.py  # Sandbox-side filesystem, staging, digest, notes, and Skill scripts
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Static native execution and judge declarations
│   ├── programs.py    # Native program assembly and bounded compiled-state loader
│   ├── turn.py        # One Turn end to end: claim, prepare, run, persist, report
│   └── children.py    # spawn_agent, spawn_agents
├── store.py          # SQL, numbered additive migrations, one pool, Turn transactions
├── optimize/          # GEPA/evaluation worker modules; imported by CLI subprocesses only
│   ├── objectives.py  # Pure scoring objectives: no MLflow, LM, or filesystem
│   ├── artifacts.py   # Identity, manifests, reports, promotion gate, approval
│   ├── validation.py  # Isolated replay engines, paired validation, trace evidence
│   ├── dataset.py     # Reviewed examples, grouped splits, canonical dataset identity
│   ├── scorers.py     # MLflow scorer sinks, judge suite, and the GEPA metric
│   ├── gepa.py        # `fleet optimize`
│   └── evaluation.py  # Eval cases, synthetic generators, gold labels, `fleet eval` runs
├── optimization_runs.py # CLI/API-neutral options, events, import, and artifact reads
├── experiments.py     # API-owned optimizer process and lifecycle manager
├── api/
│   ├── app.py         # create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and SSE envelope
│   └── routes/        # sessions (+ memory, skills), workspace, turns, experiments
├── setup.py           # Configuration draft, readiness probes, serialized setup operations
├── session.py         # Runtime, registry, leases, memory/history/Turn-record policies
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli` and `fleet web`
├── tui/
│   ├── app.py         # Fullscreen Textual application and non-TTY fallback loop
│   ├── widgets.py     # Prompt composer, command menu, collapsible steps, and Turn widget
│   ├── stream.py      # Turn SSE request and event stream parser
│   ├── setup.py       # FastAPI-backed startup checklist
│   ├── commands.py    # Slash-command table (dispatch, /help, completion)
│   ├── experiments.py # API-backed optimization lifecycle and review screen
│   ├── render.py      # Event rendering
│   ├── web.py         # Textual Serve launcher and safely quoted client command
│   └── web_client.py  # Per-browser Textual app process
└── cli.py             # `fleet` entry point: serve, cli, web, optimize, eval
```

The FastAPI app (`api/app.py`) is the primary application: a Turn enters through
`api/routes/turns.py`, runs on a worker thread, and ends with one terminal outcome.
TUI experiment routes delegate optimization to a CLI subprocess managed by
`experiments.py`; the serving process does not import `fleet_rlm.optimize`.
PostgreSQL stores API-owned and standalone CLI run lifecycle. `fleet optimize`
remains an independently callable synchronous CLI workflow. `optimization_runs.py`
is neutral to the serving boundary and holds shared options, worker events, and
legacy history import without rewriting the source files.

The package contains 61 Python files: 53 implementation modules and eight package
initializers. Resources bring the tracked source/resource target to 68 files. This
reduces navigation boundaries, not the number of application capabilities. Release
checks live in repository tooling at `scripts/release_checks.py`, outside the wheel.

### Python import changes

- `fleet_rlm.program_file.CompiledProgramFile` → `fleet_rlm.engine.programs.CompiledProgramFile`.
- `fleet_rlm.lm_usage` → `fleet_rlm.observability`.
- `fleet_rlm.store.db` → `fleet_rlm.store`; `fleet_rlm.setup.service` → `fleet_rlm.setup`.
- Snapshot definition, preflight, and operations now share `fleet_rlm.sandbox.snapshots`.
- Synthetic generators moved from `fleet_rlm.optimize.benchmarks` to `fleet_rlm.optimize.evaluation`.
- Isolated engine construction and `IsolatedFleetProgram` live in `optimize.validation`;
  identity/report helpers live in `optimize.artifacts`; `dataset_digest` lives in `optimize.dataset`.
- Saved mode/root/sub overrides use `FleetSettings.for_session`; the former harness
  methods live directly on `SessionRuntime`, with `runtime.store` for persistence.

Old submodule paths have no compatibility shims. CLI/HTTP operations and native JSON
state keys (including the isolated wrapper's `program.` prefix) remain stable.
`engine/signatures.py` remains a declaration boundary; GEPA traverses the instantiated
student, not declaration files. `contracts.py` stays a leaf. Optimizer dependencies
flow from `gepa` to dataset/validation/artifacts/scorers, never back into orchestration.

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there, including Store migration regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
