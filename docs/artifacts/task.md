# fleet-rlm Surgical Integration Tasks

- [x] **Phase 1: Evolutive Memory Integration**
  - [x] Add dependencies via `uv add` (sqlmodel, asyncpg, pgvector)
  - [x] Create `src/fleet_rlm/memory/schema.py` (TaxonomyNode, AgentMemory)
  - [x] Create `src/fleet_rlm/memory/db.py` (async engine + session factory)
  - [x] Verify via `test_db.py`
- [x] **Phase 2: Context Guards & Memory Tools**
  - [x] Inject `MAX_CHARS=2000` truncation guard into `stateful/sandbox.py`
  - [x] Create `src/fleet_rlm/core/memory_tools.py` with `@dspy.tool search_evolutive_memory`
- [x] **Phase 3: Agent Core (ReAct + RLM)** — _Pre-built in existing codebase_
  - [x] Analyze `react/` module — discovered full dual-loop already exists
  - [x] `rlm_runtime_modules.py` has 12+ `dspy.RLM` wrappers
  - [x] `delegate_sub_agent.py` handles depth-tracked recursive sub-agents
  - [x] `runtime_factory.py` provides lazy-cached module construction
  - [/] Wire `search_evolutive_memory` into `build_tool_list()` (~5 LOC)
  - [ ] Verify truncation guard covers `dspy.RLM` → `ModalInterpreter.execute()` path
- [ ] **Phase 4: API & Multiplexed WebSockets**
  - [ ] Review `src/fleet_rlm/server/routers/` and `execution_events.py`
  - [ ] Expand `StreamEvent` with `rlm_executing`, `plan_update`, `memory_update` types
  - [ ] Add WebSocket endpoint broadcasting multiplexed JSON
  - [ ] Ensure `tui-cli` and `tui-ink` backwards compatibility
- [ ] **Phase 5: The Frontend**
  - [ ] Scaffold Vite + React app in `frontend/`
  - [ ] Build Zustand stores consuming WebSocket stream
  - [ ] Build Dual-Pane UI (Chat left, Workspace right)
  - [ ] Connect TanStack Query for Neon taxonomy cache

## Planning Artifacts

- [x] Generate all phase planning documents
- [x] Generate DSPy alignment matrix
- [x] Generate industry comparison matrix (6-column)
- [x] Generate codebase assessment
- [x] Generate concept, user flow, architecture diagram artifacts
- [x] Create master `INDEX.md`
- [x] Copy all artifacts to `docs/` folder
