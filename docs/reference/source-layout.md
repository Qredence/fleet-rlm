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
│   ├── paths.py       # Session ID and workspace path validation
│   └── runtime/repl.py # Sandbox-side namespace, output capture, and bridge
├── workspace.py       # Sandbox file helpers and serializable dataset handles
├── memory_and_eval.py # Postgres VFS, DSPy engine, MLflow setup
├── engine/
│   ├── routes.py      # Route enum: direct / flex / rlm (+ legacy-name reader)
│   └── signatures.py  # Every DSPy signature and the stopping rules
├── optimize/
│   ├── artifacts.py   # Isolated rollouts, manifests, promotion gates, compiled-program loader
│   ├── dataset.py     # Reviewed examples from Postgres/MLflow, grouped splits
│   ├── metric.py      # GEPA metric
│   ├── scorers.py     # MLflow scorers and judge suite
│   ├── gepa.py        # `fleet optimize`
│   ├── evaluation.py  # Eval cases, gold labels, `fleet eval` runs
│   └── benchmarks.py  # Long-context generators and Oolong/LongCoT adapters
├── server.py          # FastAPI routes and SSE turn streaming
├── launcher.py        # starts/stops the API server and local MLflow for `fleet cli`
└── cli.py             # Rich client and server command
```

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there rather than the removed terminal client or migration checks.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
