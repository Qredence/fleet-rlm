# Phase 2 — Daytona Adapter, Agent Skills Selector & DSPy 3.5 Recursive Core

## Objective
Build the three independent domain engines (`interpreter.py`, `skills.py`, `agent.py`) that power sandboxed Python execution, context-driven `SKILL.md` progressive disclosure, recursive subagent spawning, AI SDK v1 event accumulation, and TypeSafe Jev memory consolidation.

## Files to Create
- `backend/src/fleet_rlm/interpreter.py`
- `backend/src/fleet_rlm/skills.py`
- `backend/src/fleet_rlm/agent.py`
- `backend/skills/data-analysis/SKILL.md` (1 reference skill for integration tests)
- `backend/tests/test_phase2_engine.py`

## Technical Specifications

### 1. Daytona Adapter (`backend/src/fleet_rlm/interpreter.py`)
Owns **100%** of `daytona==0.232.0` imports. No other module may import `daytona`.

#### A. Sandbox Provisioning (`ensure_session_sandbox`)
- Inputs: `daytona: AsyncDaytona`, `settings: Settings`, `session_id: UUID`, `existing_sandbox_id: str | None`.
- Returns `(sandbox: AsyncSandbox, sandbox_id: str, created: bool)` (caller persists new `sandbox_id` in `db.py` so `interpreter.py` stays independent of `db.py`).
- If `existing_sandbox_id` exists: `sandbox = await daytona.get(existing_sandbox_id)`; if `sandbox.state != "started"`, `await sandbox.start()`.
- Otherwise create via `CreateSandboxFromSnapshotParams`:
  - `snapshot=settings.daytona_snapshot`, `language="python"`, `target=settings.daytona_target`, `auto_stop_interval=settings.daytona_auto_stop_minutes`.
  - `volumes=[VolumeMount(volume_id=settings.daytona_volume_id, mount_path="/workspace", subpath=f"sessions/{session_id}")]`.

#### B. `DaytonaCodeInterpreter` (DSPy 3.5.0 `CodeInterpreter` Protocol)
- Expose `execution_instructions`, mutable `self.tools: dict[str, Callable]`, and `self.output_fields: list[dict]`.
- Bridge synchronous `start()`, `execute(code, variables)`, and `shutdown()` onto the shared FastAPI `asyncio.AbstractEventLoop` via `asyncio.run_coroutine_threadsafe(coro, self._loop).result()`.
- `start()`: creates an isolated `InterpreterContext` via `sandbox.code_interpreter.create_context(cwd="/workspace")` and injects a lightweight Python prelude registering `SUBMIT(...)` and host-tool RPC stubs (`llm_query`, `llm_query_batched`, `spawn_agent`, plus any skill-bound host/MCP tools).
- `execute(code, variables)`:
  - Serializes `variables` into the context namespace.
  - Streams stdout via `on_stdout`; intercepts private RPC marker lines, executes the corresponding host tool (never exposing host API keys inside Daytona), and delivers results back cleanly (with execution-fence protection against late replies).
  - Returns `dspy.primitives.code_interpreter.FinalOutput(submitted)` when `SUBMIT(...)` was called, otherwise concatenated stdout truncated to `max_output_chars`.
  - Raises `RuntimeError` with structured traceback on `result.error`.
- `shutdown()`: always calls `sandbox.code_interpreter.delete_context(ctx)` inside `try/finally`.
- Provide `make_interpreter_factory(...) -> Callable[[], DaytonaCodeInterpreter]` for `dspy.context(interpreter_factory=...)`.
- Also provide helper `list_new_artifacts(sandbox, known_paths)` to hash and register files written under `/workspace/artifacts` after a run finishes.

### 2. Agent Skills & TypeSafe Jev Selector (`backend/src/fleet_rlm/skills.py`)
Implements open **Agent Skills (`SKILL.md`)** progressive disclosure with zero mutable per-session state.

- **`SkillDefinition` (frozen dataclass):** `name`, `description`, `body`, `path`, `allowed_tools: tuple[str, ...]`, `mcp_servers: tuple[str, ...]`.
- **`SkillRegistry.load(skills_dir: Path)`:** Called once at FastAPI `lifespan` startup. Scans `skills/*/SKILL.md`, parses YAML frontmatter (`name`, `description`, optional `allowed_tools`, `mcp_servers`), keeps the body ready in memory, and freezes the registry.
- **Context-Driven Selector (`select_skills`):**
  - Supports explicit user overrides (`pinned_skills: list[str]`, e.g., `@data-analysis` from Vite UI) bypassing Jev deterministically.
  - Otherwise invokes a single `dspy.Predict` signature on `dspy.LM(settings.decision_model)` (`typesafe:jev-latest`) combining:
    - `needs_skill: Noul[(True, ...), (False, ...)]`
    - `primary_skill: Choice[tuple((s.name, {"what": s.description}) for s in registry.skills)]`
  - Guard against Jev's 255-option `Choice` limit (if registry exceeds 40 skills, pre-filter by keyword overlap before calling Jev).
  - Activate top 1–2 skills where `needs_skill` is `True` and `primary_skill.probabilities[name] >= threshold` (default `0.25`).
  - Support loading a `ReAnchor`-calibrated JSON state from `settings.skill_selector_path` if present.
  - Returns `SkillSelection(active_skills, scores, allowed_tools)`.

### 3. DSPy 3.5 Recursive Agent, AI SDK v1 Encoder & Memory Gate (`backend/src/fleet_rlm/agent.py`)

#### A. Signature & RLM Construction
- Define `WorkspaceTask(dspy.Signature)` strictly avoiding DSPy 3.5's reserved RLM names (`history`, `repl_entry`, `variables_info`, `iteration`):
  - Inputs: `task: str`, `files_manifest: list[dict]`, `memories: list[str]`, `active_skills: str`.
  - Outputs: `answer: str`, `artifact_paths: list[str]`.
- Implement `bound_history(history: dspy.History, max_turns: int, max_chars: int) -> dspy.History` passed to `dspy.RLM(..., history_processor=...)`.
- `build_program(settings, tools)` builds `dspy.RLM(WorkspaceTask, max_iters=..., max_llm_calls=..., max_output_chars=..., sub_lm=..., tools=..., history_processor=...)` and loads `settings.optimized_program_path` if configured.

#### B. Recursive `spawn_agent` Tool
- Enforces `depth < settings.rlm_max_depth`.
- Calls a provided async callback `on_child_start` / `on_child_update` / `on_child_finish` (wired in `api.py` to persist child `runs` rows in Neon).
- Runs a nested `dspy.RLM` invocation in its own fresh `DaytonaCodeInterpreter` (`InterpreterContext`), streaming live child `ui_parts` snapshots upward so the parent emits `tool-output-available` with `preliminary: true` for shadcn's recursive `Subagent` UI.

#### C. `UiPartAccumulator` & AI SDK UI Message Stream v1 Encoder
Maintain one deterministic translator that simultaneously emits SSE event dicts **and** builds the persisted `ui_parts` array matching `@ai-sdk/react`'s `UIMessage.parts`:
- Skills selected -> `data-skills` part (`{ skills: [{ name, score }] }`).
- Reasoning step -> `reasoning-start` / `reasoning-delta` / `reasoning-end`.
- Daytona Python execution -> `tool-input-start` (`toolName: "python_exec"`, `inputEncoding: "text"`) -> `tool-input-delta` -> `tool-output-available` (or `tool-output-error`).
- Recursive `spawn_agent` / `llm_query` -> `tool-input-available` (`toolName: "subagent"`) -> repeated `tool-output-available` (`preliminary: true`, `output: { id, role: "assistant", parts: [...] }`) -> final `preliminary: false`.
- File tree update -> `data-file-tree` part.
- Final answer -> `text-start` / `text-delta` / `text-end` -> `finish`.

#### D. Two-Stage Post-Turn Memory Consolidation (`consolidate_memory`)
Runs after a completed root turn (in `api.py` post-stream cleanup, zero daemon):
- **Stage 1 (TypeSafe Jev `MemoryGate`):** Uses `Noul` (`worth_saving`) + `Choice` (`action`: `insert | update | skip`; `scope`: `global | session`) against compact `(task, answer)` and top-3 existing Neon memories. Load calibrated weights from `settings.memory_gate_path` if present.
- **Stage 2 (Generative `DistillMemory` on `sub_llm_model`):** Invoked **only** if `worth_saving` is `True` and `action != "skip"`. Produces 1–2 neutral sentences + optional `supersedes_id`.
- Auto-approve only when `worth_saving.probability >= 0.90`; otherwise save with `approved = False` for UI review.

## Exit Gate (Must Pass Before Phase 3)
```bash
cd backend
uv run ruff check .
uv run lint-imports
uv run pytest tests/test_phase2_engine.py
```
- **Acceptance criteria:**
  1. Unit tests verify `UiPartAccumulator` outputs valid AI SDK v1 frame sequences (including nested `preliminary: true` subagent updates) and lossless `ui_parts` snapshots.
  2. Unit tests verify `SkillRegistry` frontmatter parsing, `@skill` override, and Jev selector gating (mocked + live optional marker).
  3. End-to-end integration test runs a 2-turn RLM conversation over a sample CSV in Daytona with 1 recursive `spawn_agent` call, verifies `dspy.History` JSON reload across fresh `InterpreterContext`s, verifies `MemoryGate` decision output, and logs a trace to local MLflow 3.18.

## Implementation status

- Implemented the Daytona adapter, immutable Skill registry and Jev selector, native DSPy RLM with recursive lifecycle callbacks, AI SDK v1 UI parts, and the two-stage memory gate. The data-analysis Skill and opt-in two-turn CSV scenario are included.
- Daytona 0.232.0 `run_code` ignores stdout callback return values and exposes no input channel. Tool requests use private stdout records. Responses use execution-scoped files published by atomic rename inside the sandbox. Recursive workspace runs use `spawn_agent` so they share the depth limit and lifecycle callbacks.
- Unapproved memory updates are saved as separate candidates to preserve the approved row during review. The Phase 1 schema updates existing rows in place and has no supersession field.
- `uv sync --python 3.13`, `uv run ruff check .`, and `uv run lint-imports` passed. `uv run pytest tests/test_phase2_engine.py` passed 13 offline cases and skipped the live scenario. AI SDK 7.0.179 replayed the v1 chunks into identical persisted parts. Local MLflow 3.18.0 recorded RLM spans from both offline turns.
- Repository-wide `make check` stopped in `check-docs` on the existing broken documentation links.
- Live Daytona, generative-model, Jev, and two-turn MLflow trace verification are pending. Live Neon migration and locking verification are still pending because the pooled and direct URLs are absent. The CSV scenario uses a temporary local MLflow 3.18 SQLite store and runs with `FLEET_RUN_PHASE2_LIVE=1` when authorized and configured.
