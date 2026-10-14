# Source layout

The active runtime is deliberately compact:

```text
src/fleet_rlm/
├── config.py          # FleetSettings and model construction
├── skills.py          # User Skill validation, sync, and catalog
├── interpreter.py     # Daytona session workspace and Python execution
├── memory_and_eval.py # Postgres VFS, DSPy engine, MLflow, GEPA
├── server.py          # FastAPI routes and SSE turn streaming
└── cli.py             # Rich client and server command
```

The corresponding implementation areas are covered by `tests/`. Repository
commands are defined in the root `Makefile`; CI runs the Lite quality targets
there rather than the removed terminal client or migration checks.

User Skills are managed from `.fleet/skills/`. `.agents/skills/` belongs to
developer tooling and must not be treated as a user Skill source.
