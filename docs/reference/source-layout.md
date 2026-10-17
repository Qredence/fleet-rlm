# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config.py          # FleetSettings and model construction
├── dspy_support.py    # Pinned DSPy adapter and typed routing compatibility
├── skills.py          # User Skill validation, sync, and catalog
├── sandbox/
│   ├── manager.py     # Daytona root/child sandboxes, budgets, workspace files, learned notes
│   ├── interpreter.py # DSPy CodeInterpreter and host-tool dispatch
│   ├── repl.py        # run_code transport and stdout bridge protocol
│   └── runtime/repl.py # Sandbox-side namespace, output capture, and bridge
├── workspace.py       # Session ID and workspace path validation, serializable dataset handles
├── engine/
│   ├── signatures.py  # Route enum (direct / flex / rlm), every DSPy signature, the stopping rules
│   ├── programs.py    # The Turn program: route selection, then direct / Flex / RLM
│   ├── turn.py        # One Turn end to end: claim, prepare, run, persist, report
│   └── children.py    # spawn_agent, spawn_agents
├── store/
│   └── db.py          # Store, numbered additive migrations, one pool, Turn commits
├── optimize/
│   ├── artifacts.py   # Isolated rollouts, manifests, promotion gate, approval, compiled-program loader
│   ├── dataset.py     # Reviewed examples from Postgres/MLflow, grouped splits
│   ├── scorers.py     # MLflow scorers, judge suite, and the GEPA metric
│   ├── gepa.py        # `fleet optimize`
│   ├── evaluation.py  # Eval cases, gold labels, `fleet eval` runs
│   └── benchmarks.py  # Long-context generators and Oolong/LongCoT adapters
├── api/
│   ├── app.py         # create_app, lifespan, routers, health
│   ├── deps.py        # Dependencies and route-boundary error translation
│   ├── schemas.py     # Request and response models
│   ├── events.py      # DSPy activity callback and SSE envelope
│   └── routes/        # sessions (+ memory, skills), workspace, turns
├── session.py         # Per-Session runtime and registry
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli`
├── tui/
│   ├── app.py         # Prompt loop and Turn stream reader
│   ├── commands.py    # Slash-command table (dispatch, /help, completion)
│   └── render.py      # Event rendering
└── cli.py             # `fleet` entry point: serve, cli, optimize, eval
```

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there, including Store migration regressions.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
