# Source layout

Runtime code lives in [`src/fleet_rlm/`](../../src/fleet_rlm/).
See [architecture](../../ARCHITECTURE.md) for request flow and trust boundaries.

| Path | Responsibility |
| --- | --- |
| `cli.py`, `launcher.py`, `tui/` | CLI entry point, owned local services, Textual/Rich clients and browser transport |
| `api/` | FastAPI routes, request/response schemas and versioned SSE envelopes |
| `session.py` | Session registry, leases, history, memory and idempotent Turn results |
| `config/`, `models.py` | Frozen settings, provenance, role models and native DSPy provider declarations |
| `contracts.py` | Shared route vocabulary, input names and stopping rules |
| `engine/programs.py`, `decisions.py`, `reasoning.py`, `children.py` | Native learned graph: triage, Skills, Direct/RLM/Flex and child policies |
| `engine/invocation.py` | Shared host construction, admission, invocation and evidence for serving and isolated rollouts |
| `engine/turn*.py` | Turn preparation, execution, commit and terminal outcome |
| `engine/compiled.py` | Complete compiled-program validation and loading |
| `sandbox/` | Daytona lifecycle, budgets, interpreter, delegation, workspace I/O and snapshot operations |
| `sandbox/runtime/` | Sandbox-side REPL and host bridge |
| `store/` | PostgreSQL schema, transactions, sessions, chat, replay blobs, leases and job ledger |
| `skills.py`, `bundled_skills/` | User Skill validation/sync and packaged task Skills |
| `workspace.py`, `replay.py`, `execution_evidence.py` | Dataset handles, immutable replay integrity and host-collected evidence |
| `observability.py` | Redaction, usage receipts, callbacks and MLflow tracing |
| `optimize/` | Curation, metrics, GEPA/ReAnchor, isolated validation, artifacts and approval |
| `optimization_runs.py`, `experiments.py` | Shared job options/artifacts and API-owned worker supervision |

The serving runtime never imports `fleet_rlm.optimize`. The API runs optimization
in a CLI subprocess; review/status routes may lazily read dataset/evaluation helpers.
The worker owns running stages and counters; the API arbitrates managed terminal
outcomes after exit. Shared contracts stay outside optimizer orchestration.

## Tests and tooling

| Area | Offline suite |
| --- | --- |
| HTTP/SSE | `tests/api/test_turns.py` |
| Runtime/configuration/Skills | `tests/engine/test_runtime.py` |
| Daytona transport/lifecycle | `tests/sandbox/test_sandbox.py` |
| GEPA/workers/approval | `tests/optimize/test_optimization.py` |
| Curation/scorers/benchmarks | `tests/optimize/test_evaluation.py` |
| CLI/Textual/browser | `tests/tui/test_clients.py` |
| Persistence | `tests/store/test_store.py` |
| CI/publication | `tests/test_release_checks.py` |

Run `uv run pytest tests/<area>/<suite>.py -k <contract>` for focused checks.
The [Makefile](../../Makefile) defines quality commands; CI discovers test files
dynamically. Live PostgreSQL cases require explicit opt-in. Offline tests use
scripted providers, an emulated sandbox and disposable MLflow storage.

User Skills belong in `.fleet/skills/`; `.agents/skills/` is developer guidance.
