# File Splitting Walkthrough

## Summary

Split oversized modules to comply with the ~600 LOC guideline:

| File                  | Before | After | New File(s)                                                  |
| --------------------- | ------ | ----- | ------------------------------------------------------------ |
| `runners.py`          | 798    | 258   | `runners_demos.py` (574)                                     |
| `cli.py`              | 976    | 595   | `cli_demos.py` (416)                                         |
| `test_react_agent.py` | 935    | 400   | `test_react_streaming.py` (252), `test_react_tools.py` (452) |

## Changes Made

### runners.py → runners.py + runners_demos.py

- Extracted 9 demo runner functions to [runners_demos.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/runners_demos.py)
- Added re-exports for backward compatibility in [runners.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/runners.py)

### cli.py → cli.py + cli_demos.py

- Extracted 9 CLI demo/diagnostic commands to [cli_demos.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/cli_demos.py)
- Kept core commands in [cli.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/cli.py)

### test_react_agent.py → 3 files

- **[test_react_agent.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_react_agent.py)** (400 LOC) — core agent construction, `forward()`, `dspy.Tool` wrappers, signature generics, context manager, history/reset, Phase 1 tests
- **[test_react_streaming.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_react_streaming.py)** (252 LOC) — `chat_turn_stream`, `iter_chat_turn_stream`, cancellation, fallback-on-error
- **[test_react_tools.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_react_tools.py)** (452 LOC) — `load_document` (text/PDF/directory), `list_files`, `read_file_slice`, `find_files`, PDF extraction, long-document analysis

### Stateful Memory Tools

Implemented Letta-style memory management using Modal Volumes:

- **New Tools in `tools_sandbox.py`**:
  - `memory_read(path)`: Read files from persistent volume
  - `memory_write(path, content)`: Write files with auto-sync and commit
  - `memory_list(path)`: List volume contents
- **Verification**:
  - Created **[`test_memory_tools.py`](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_memory_tools.py)**
  - Verified code generation and interpreter interactions (sync/commit)

### Core Memory (Tier 1)

Implemented **In-Context Memory Blocks** (Letta-style) directly in the agent:

- **Host-Side Logic**: Core memory (`persona`, `human`, `scratchpad`) is a Python dict on the agent instance.
- **Prompt Injection**: Injected into the `RLMReActChatSignature` via `fmt_core_memory()`.
- **Safety Limits**: Enforced character limits (`_core_memory_limits`) to prevent context explosion.
- **Tools**: `core_memory_append` and `core_memory_replace` allow the agent to self-modify its context.
- **Persistence**: Synced via `export_session_state` (WebSocket session manager), avoiding fragile volume executions.

### Pre-existing Test Fixes (discovered during split)

| Issue                                  | Fix                                                                |
| -------------------------------------- | ------------------------------------------------------------------ |
| `max_iters` default changed 15→10      | Updated assertion to `== 10`                                       |
| `parallel_semantic_map` params renamed | Changed `chunk_size`/`max_workers` → `chunk_strategy`/`max_chunks` |
| `stream_error` key removed             | Assert via `status_messages` instead                               |
| Event kind `chunk` renamed             | Changed to `assistant_token`                                       |
| `is_cancelled` param renamed           | Changed to `cancel_check`                                          |

## Documentation & Visualization

Responding to user request for new artifacts, created comprehensive documentation in `docs/`:

- **[`concepts.md`](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/concepts.md)**: Defines core architecture (Agent, RLM, Sandbox, Tools).
- **[`user_flows.md`](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/user_flows.md)**: Visualizes key interactions (Chat, Tool Use, RLM Delegation, Editing) using Mermaid sequence diagrams.
- **[`architecture.md`](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/architecture.md)**: Visualizes system components, network topology, and module hierarchy.

## Verification

- **Ruff**: All 3 test files pass `ruff check` with zero errors ✅
- **Unit tests**: Full suite passes (`uv run pytest tests/unit/ -x -q`) — 204 tests ✅
