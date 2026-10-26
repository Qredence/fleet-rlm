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
│   ├── manager.py     # Daytona root/child sandboxes, budgets, workspace files, learned notes
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   ├── snapshots/     # Packaged definition, runtime preflight, operator operations
│   └── runtime/repl.py # Sandbox-side namespace, output capture, and bridge
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Every DSPy signature GEPA tunes (route vocabulary lives in contracts)
│   ├── programs.py    # The Turn program: route selection, then direct / Flex / RLM
│   ├── turn.py        # One Turn end to end: claim, prepare, run, persist, report
│   └── children.py    # spawn_agent, spawn_agents
├── store/
│   └── db.py          # Store, numbered additive migrations, one pool, Turn commits
├── optimize/          # Offline tooling: never imported by the API or the engine
│   ├── objectives.py  # Pure scoring objectives: no MLflow, LM, or filesystem
│   ├── artifacts.py   # Isolated rollouts, manifests, promotion gate, approval
│   ├── dataset.py     # Reviewed examples from Postgres/MLflow, grouped splits
│   ├── scorers.py     # MLflow scorer sinks, judge suite, and the GEPA metric
│   ├── gepa.py        # `fleet optimize`
│   ├── evaluation.py  # Eval cases, gold labels, `fleet eval` runs
│   └── benchmarks.py  # Synthetic long-context task generators
├── api/
│   ├── app.py         # create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and SSE envelope
│   └── routes/        # sessions (+ memory, skills), workspace, turns
├── setup/             # Configuration draft, readiness probes, serialized setup operations
├── session.py         # Per-Session runtime and registry
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli`
├── tui/
│   ├── app.py         # Prompt loop and Turn stream reader
│   ├── setup.py       # FastAPI-backed startup checklist
│   ├── commands.py    # Slash-command table (dispatch, /help, completion)
│   └── render.py      # Event rendering
└── cli.py             # `fleet` entry point: serve, cli, optimize, eval
```

The FastAPI app (`api/app.py`) is the primary application: a Turn enters through
`api/routes/turns.py`, runs on a worker thread, and ends with one terminal outcome.
`fleet optimize` and `fleet eval` are offline tooling, and importing
`fleet_rlm.api.app` does not load `fleet_rlm.optimize`.

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there, including Store migration regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
