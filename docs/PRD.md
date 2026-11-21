# Fleet-RLM — Product Requirements Document & Architecture Specification

## 1. Product Vision & Reframing

**Fleet-RLM** is a **stateless, recursive agentic system** built on Recursive Language Models (`dspy.RLM`). Rather than cramming large documents, spreadsheets, or codebases into an LLM context window, files live on an isolated **Daytona** workspace. The agent writes and executes Python code to inspect, transform, and verify data, and dynamically spawns recursive sub-LM queries (`llm_query`, `llm_query_batched`) or isolated child agents (`spawn_agent`) up to a bounded recursion depth.

### Core Architectural Shift
Fleet-RLM is **not** a local stateful runtime or process daemon.
- **Compute is stateless:** Any FastAPI worker can handle any request for any session. Zero in-process `SessionRuntime` caches, zero PostgreSQL advisory-lock heartbeats, zero setup HTTP servers, and zero embedded MLflow process supervision.
- **State is durable and minimal:** Neon Postgres 18 holds conversation history, the recursive execution tree (`runs`), file manifests, and unified memories. Daytona holds workspace files and artifacts.
- **Native framework primitives only:** Never duplicate what DSPy 3.5.0, FastAPI 0.165.0, Daytona 0.232.0, Neon Postgres 18, MLflow 3.18.0, or shadcn/ui Base UI AI Elements already provide.

---

## 2. System Capabilities

1. **Statefulness (Stateless Compute, Stateful Agent):**
   - Conversations persist serialized DSPy 3.5 `History` plus exact AI SDK `ui_parts` in Neon Postgres 18.
   - Uploaded inputs (`/workspace/files`) and generated outputs (`/workspace/artifacts`) persist across turns and sandbox stop/resume cycles on a single shared Daytona Volume isolated per session via `subpath`.
   - Live REPL RAM intentionally resets per run via fresh Daytona `InterpreterContext` instances to prevent leaked globals.
2. **Programmatic Long Context:**
   - Handles arbitrarily large files via sandboxed Python execution rather than prompt stuffing.
   - Supports map-reduce over large unstructured text (`llm_query_batched`) and programmatic image inspection/cropping (`pillow` + DSPy 3.5 RLM image support).
   - Multi-turn chat stays within bounded token budgets via `dspy.RLM(history_processor=...)`.
3. **4-Tier Memory System:**
   - **Working memory:** Ephemeral Daytona `InterpreterContext` RAM + RLM trajectory.
   - **Short-term episodic:** `sessions.history_json` (`dspy.History`) + `/workspace` artifacts.
   - **Long-term declarative:** Unified Neon `memories` table (`session_id IS NULL` for global, `tsvector` GIN search, `vector` extension ready) with automatic two-stage post-turn consolidation gated by **TypeSafe Jev** (`Noul`/`Choice`).
   - **Long-term procedural:** Offline `dspy.GEPA` and `dspy.experimental.ReAnchor` optimization producing versioned read-only JSON artifacts.
4. **Neuro-Symbolic Recursive Reasoning:**
   - Code-as-thought verification against real Python execution outputs and tracebacks.
   - Runtime recursive decomposition (`spawn_agent`, `llm_query_batched`) up to `rlm_max_depth`.
5. **Extensible Skills (`SKILL.md`) + MCP:**
   - Read-only Agent Skills (`skills/*/SKILL.md` + `scripts/`, `references/`, `assets/`) discovered at startup and baked into `/opt/skills` in the Daytona snapshot.
   - Context-driven progressive disclosure via **TypeSafe Jev** (`Noul` + `Choice`), binding only relevant skill instructions and scoped host/MCP tools per turn (with explicit `@skill` user override).

---

## 3. Pinned Technology Stack (November 2026 Baseline)

| Component | Exact Pin / Target | Purpose |
| :--- | :--- | :--- |
| **Python** | `>=3.13,<3.14` (standard GIL `python:3.13-slim`, NOT `3.13t`) | Host API + Daytona snapshot parity |
| **FastAPI** | `fastapi[standard]>=0.165.0` | Stateless HTTP + native `fastapi.sse.EventSourceResponse` over `POST` |
| **DSPy** | `dspy==3.5.0` | `dspy.RLM`, `dspy.Flex`, `dspy.History`, `interpreter_factory`, `dspy.streamify`, Decision Types (`Noul`, `Choice`, `Score`, `ReAnchor`), `dspy.GEPA` |
| **Decision Model** | `dspy.LM("typesafe:jev-latest")` (pin snapshot after `ReAnchor`) | Fast, probability-calibrated Skill selection & Memory gating |
| **Daytona** | `daytona==0.232.0` *(stable)* | `AsyncDaytona`, `VolumeMount(subpath=...)`, `AsyncCodeInterpreter` contexts |
| **Database** | **Neon Postgres 18** (`sqlalchemy[asyncio]>=2.1.9`, `alembic>=1.20.1`, `psycopg[binary]>=3.3.9`) | `uuidv7()`, recursive `runs`, partial unique index lock, `tsvector`, DB branching |
| **Observability** | **MLflow `>=3.18.0`** (external server) | `mlflow.dspy.autolog()` + offline GEPA compile/eval tracking |
| **Frontend** | **Vite + React + TypeScript + Tailwind + shadcn/ui (Base UI AI Elements)** + `@ai-sdk/react` | `useChat` consuming `x-vercel-ai-ui-message-stream: v1` directly (`Trace`, `Subagent`, `Code Block`, `File Tree`, `Badge`) |

---

## 4. Target Repository Structure & Module Boundaries

The entire backend consists of **7 focused Python modules** enforcing a strict one-way DAG (`import-linter` enforced in CI):

```text
fleet-rlm/
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   ├── snapshot/
│   │   └── Dockerfile                 # python:3.13-slim + dspy==3.5.0 + /opt/skills
│   ├── skills/                        # Agent Skills (SKILL.md folders)
│   ├── src/fleet_rlm/
│   │   ├── config.py                  # Flat pydantic-settings (env / .env)
│   │   ├── db.py                      # Neon Postgres 18 ORM, pooling, run lock, memories
│   │   ├── interpreter.py             # 100% of Daytona SDK + DSPy CodeInterpreter adapter
│   │   ├── skills.py                  # SKILL.md loader + TypeSafe Jev selector + tool scoping
│   │   ├── agent.py                   # DSPy 3.5 RLM, UiPartAccumulator, AI SDK v1 encoder, Jev MemoryGate
│   │   ├── api.py                     # FastAPI 0.165 app, lifespan, Depends, 7 route groups
│   │   └── optimize.py                # Offline CLI: Neon branch + MLflow 3.18 + GEPA + ReAnchor
│   └── tests/
├── frontend/                          # Vite + React + shadcn/ui Base UI AI Elements
└── docker-compose.yml                 # Local dev MLflow 3.18 server only
```

### Dependency Rules (Enforced by `import-linter`)
1. `config.py` imports no internal module.
2. `db.py`, `interpreter.py`, `skills.py`, and `agent.py` may import `config.py`, and **never** import `fastapi` or `api.py`.
3. `db.py`, `interpreter.py`, and `skills.py` are strictly independent of one another.
4. Only `interpreter.py` imports `daytona`. Only `api.py` imports `fastapi`.
5. `optimize.py` never imports `api.py`.

---

## 5. Non-Negotiable Engineering Guardrails (Read Before Coding)

1. **No In-Process Session State:** Never introduce a module-level or `app.state` dictionary keyed by `session_id` holding sandboxes, locks, or runtimes.
2. **No Legacy DSPy APIs:**
   - Do **not** import `dspy.experimental.TypeSafe` (removed in 3.5.0). Use `dspy.LM("typesafe:jev-latest")`.
   - Never name custom Signature inputs/outputs `history`, `repl_entry`, `variables_info`, or `iteration` (reserved by `dspy.RLM` in 3.5.0).
   - Never place `Noul`/`Choice`/`Score` outputs inside a `dspy.RLM` signature (unsupported); keep them in standalone `dspy.Predict` gates.
   - Always bake `dspy==3.5.0` into the Daytona snapshot (required by DSPy 3.5 custom interpreters).
3. **No Legacy FastAPI Patterns:**
   - Run `uvx library-skills` (FastAPI 0.162.2+) if available so coding agents follow 0.165 idioms.
   - Use `fastapi.sse.EventSourceResponse` + `ServerSentEvent`, **not** Starlette `StreamingResponse`.
   - Use `max_body_size` and `form_limits` on the app/routes, **not** custom upload-size middleware.
   - Never hold a `Depends(get_db)` session open across an SSE stream; use short `async with sessionmaker()` blocks inside the stream generator and `Depends(get_db, scope="function")` on REST routes.
4. **Neon Postgres 18 PgBouncer Discipline:**
   - Use `FLEET_DATABASE_URL` (`-pooler`) for runtime with `connect_args={"prepare_threshold": None}`, `pool_size=2`, `max_overflow=5`, `pool_pre_ping=True`, `pool_recycle=300`.
   - Use `FLEET_DATABASE_DIRECT_URL` (non-pooled) strictly for Alembic migrations.
   - Never use `pg_advisory_lock`, `LISTEN/NOTIFY`, or SQL `PREPARE`.
5. **Daytona Isolation & 2.0 Forward Compatibility:**
   - Target stable `daytona==0.232.0` (`AsyncDaytona`, `VolumeMount(..., subpath=...)`, `sandbox.code_interpreter`).
   - Keep every Daytona import inside `interpreter.py` so migrating to Daytona 2.0 (`d.v2`, `sandbox.interpreter`, `sandbox.fork()`, `network_policy`, `replace_secrets`) is a single-file change.
   - Never pass host API keys into Daytona sandbox env vars.
6. **Zero Custom Wire Protocols:**
   - FastAPI emits the standard AI SDK UI Message Stream Protocol (`x-vercel-ai-ui-message-stream: v1`, ending with `data: [DONE]`).
   - Never write a BFF proxy or custom SSE parser in Vite.
7. **No Per-Turn Sandbox Probing:**
   - Compute file SHA-256 hashes at upload ingress in `api.py` and store in Neon `files`.
   - Trust the pinned Daytona snapshot for installed Python packages and `/opt/skills`.
