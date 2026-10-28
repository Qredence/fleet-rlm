# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config.py          # FleetSettings and model construction
├── contracts.py       # Route vocabulary, stopping rules, runtime input names, decision evidence
├── adapters.py        # Fleet's DSPy ChatAdapter: raw-Python fence and one format re-ask
├── program_file.py    # Compiled-program loading: bounded, atomic, rolled back on failure
├── execution_evidence.py # Gold schema plus bounded host-collected artifact/child evidence
├── skills.py          # User Skill validation, sync, and catalog
├── sandbox/
│   ├── manager.py     # Daytona root/child sandboxes, lifecycle, and budgets
│   ├── workspace_io.py # Session workspace files, context datasets, and learned notes mixin
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   ├── snapshots/     # Packaged definition, runtime preflight, operator operations
│   └── runtime/
│       ├── repl.py    # Sandbox-side namespace, output capture, and bridge
│       └── fs_ops.py  # Sandbox-side filesystem, staging, digest, notes, and Skill scripts
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Every DSPy signature GEPA tunes (route vocabulary lives in contracts)
│   ├── programs.py    # The Turn program: route selection, then direct / Flex / RLM
│   ├── turn.py        # One Turn end to end: claim, prepare, run, persist, report
│   └── children.py    # spawn_agent, spawn_agents
├── store/
│   └── db.py          # Store, numbered additive migrations, one pool, Turn commits
├── optimize/          # GEPA/evaluation worker modules; imported by CLI subprocesses only
│   ├── objectives.py  # Pure scoring objectives: no MLflow, LM, or filesystem
│   ├── artifacts.py   # Isolated rollouts, manifests, promotion gate, approval
│   ├── validation.py  # Paired per-case validation and trace evidence
│   ├── dataset.py     # Reviewed examples from Postgres/MLflow, grouped splits
│   ├── scorers.py     # MLflow scorer sinks, judge suite, and the GEPA metric
│   ├── gepa.py        # `fleet optimize`
│   ├── evaluation.py  # Eval cases, gold labels, `fleet eval` runs
│   └── benchmarks.py  # Synthetic long-context task generators
├── optimization_runs.py # CLI/API-neutral options, events, import, and artifact reads
├── experiments.py     # API-owned optimizer process and lifecycle manager
├── api/
│   ├── app.py         # create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and SSE envelope
│   └── routes/        # sessions (+ memory, skills), workspace, turns, experiments
├── setup/             # Configuration draft, readiness probes, serialized setup operations
├── session.py         # Per-Session runtime and registry
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

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there, including Store migration regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
