# Fleet-RLM Restructuring Implementation Plan

## Overview
Transform `src/fleet_rlm/` from 18 flat modules to a layered, maintainable architecture while **preserving 100% backward compatibility** through `__init__.py` re-exports.

**Estimated Duration**: 3-4 days  
**Risk Level**: Low (incremental, tested at each phase)  
**Rollback Strategy**: Git revert per phase

---

## Phase 0: Preparation (Day 1 Morning - 2 hours)

### 0.1 Pre-flight Checklist
```bash
# Verify clean git state
git status
git log --oneline -5

# Create feature branch
git checkout -b refactor/organize-structure

# Run full test suite baseline
uv run pytest tests/unit -q
# Expected: 158 passed
```

### 0.2 Create Directory Structure
```
src/fleet_rlm/
├── core/                    # NEW
├── chunking/               # NEW
├── signatures/             # NEW
├── react/                  # NEW
│   └── tools/              # NEW
├── stateful/               # NEW
├── runners/                # NEW
├── cli/                    # NEW
│   └── commands/           # NEW
└── utils/                  # NEW
```

**Action**: Create all `__init__.py` files with re-export stubs

**Success Criteria**:
- `python -c "import fleet_rlm"` succeeds
- All public imports still work

---

## Phase 1: Core Layer Extraction (Day 1 Afternoon - 4 hours)
**Risk**: Low | **Dependencies**: None (foundation layer)

### 1.1 Extract Sandbox Utilities from driver.py

**Files to Create**:
- `src/fleet_rlm/core/sandbox_utils.py` (extracted from driver.py lines ~258-386)

**Content to Extract** (from driver.py):
- `peek()` - line 258
- `grep()` - line 270
- `chunk_by_size()` - line 282
- `chunk_by_headers()` - line 300
- `chunk_by_timestamps()` - line 312
- `chunk_by_json_keys()` - line 324
- `add_buffer()`, `get_buffer()` - lines 337-350
- `save_to_volume()`, `load_from_volume()` - lines 353-375
- `workspace_list()` - line 378
- `log_execution()` - line 386

**Files to Modify**:
- `src/fleet_rlm/driver.py`: Remove extracted functions, keep only:
  - `sandbox_driver()` main entry point
  - `SandboxResult` dataclass
  - `llm_query()` and `llm_query_batched()` wrappers

### 1.2 Move Core Modules

**Files to Move** (using `git mv`):
- `config.py` → `core/config.py`
- `driver.py` → `core/driver.py`
- `interpreter.py` → `core/interpreter.py`

**Files to Create**:
- `core/__init__.py` with re-exports:
  ```python
  from .config import configure_planner_from_env, get_planner_lm_from_env
  from .driver import sandbox_driver, SandboxResult
  from .interpreter import ModalInterpreter
  from .sandbox_utils import (
      peek, grep, chunk_by_size, chunk_by_headers,
      chunk_by_timestamps, chunk_by_json_keys,
      add_buffer, get_buffer, save_to_volume, load_from_volume,
      workspace_list, log_execution,
  )
  ```

### 1.3 Update Root __init__.py
Add backward-compatible imports:
```python
# Core (re-exported for backward compatibility)
from .core import (
    configure_planner_from_env,
    get_planner_lm_from_env,
    sandbox_driver,
    ModalInterpreter,
)
```

**Testing**:
```bash
uv run pytest tests/unit/test_config.py tests/unit/test_driver_helpers.py \
  tests/unit/test_driver_protocol.py tests/unit/test_react_agent.py -v
# Expected: All pass
```

**Rollback**: `git revert HEAD` (single commit)

---

## Phase 2: Chunking & Signatures Subpackages (Day 2 Morning - 3 hours)
**Risk**: Low | **Dependencies**: None (independent modules)

### 2.1 Create chunking/ Subpackage

**Files to Create**:
- `chunking/size.py` - `chunk_by_size()`
- `chunking/headers.py` - `chunk_by_headers()`
- `chunking/timestamps.py` - `chunk_by_timestamps()`
- `chunking/json_keys.py` - `chunk_by_json_keys()`
- `chunking/__init__.py`:
  ```python
  from .size import chunk_by_size
  from .headers import chunk_by_headers
  from .timestamps import chunk_by_timestamps
  from .json_keys import chunk_by_json_keys
  ```

**Files to Modify**:
- Move content from `chunking.py` to new files
- Delete original `chunking.py`

**Update Root __init__.py**:
```python
# Chunking (re-exported from subpackage)
from .chunking import (
    chunk_by_size,
    chunk_by_headers,
    chunk_by_timestamps,
    chunk_by_json_keys,
)
```

### 2.2 Create signatures/ Subpackage

**Files to Create**:
- `signatures/base.py` - Base signature classes if any
- `signatures/extraction.py` - ExtractArchitecture, ExtractAPIEndpoints, etc.
- `signatures/analysis.py` - AnalyzeLongDocument, SummarizeLongDocument
- `signatures/__init__.py` with all exports

**Files to Modify**:
- Move content from `signatures.py` to new files
- Delete original `signatures.py`

**Update Root __init__.py** with signature re-exports

**Testing**:
```bash
uv run pytest tests/unit/test_chunking.py -v
# Expected: All chunking tests pass
```

---

## Phase 3: React System Reorganization (Day 2 Afternoon - 4 hours)
**Risk**: Medium | **Dependencies**: Core layer (Phase 1 complete)

### 3.1 Create react/tools/ Subpackage

**Split react_tools.py into**:

- `react/tools/documents.py` (lines ~144-209):
  - `load_document()`
  - `set_active_document()`
  - `list_documents()`

- `react/tools/filesystem.py` (lines ~210-315):
  - `list_files()`
  - `read_file_slice()`
  - `find_files()`

- `react/tools/chunking.py` (lines ~317-395):
  - `chunk_host()`
  - `chunk_sandbox()`

- `react/tools/analysis.py` (lines ~397-480):
  - `analyze_long_document()`
  - `summarize_long_document()`
  - `extract_from_logs()`
  - `parallel_semantic_map()`

- `react/tools/buffers.py` (lines ~482-540):
  - `read_buffer()`
  - `clear_buffer()`
  - `save_buffer_to_volume()`
  - `load_text_from_volume()`

- `react/tools/__init__.py`:
  ```python
  from .documents import load_document, set_active_document, list_documents
  from .filesystem import list_files, read_file_slice, find_files
  from .chunking import chunk_host, chunk_sandbox
  from .analysis import analyze_long_document, summarize_long_document, extract_from_logs, parallel_semantic_map
  from .buffers import read_buffer, clear_buffer, save_buffer_to_volume, load_text_from_volume
  from .base import build_tool_list, list_react_tool_names
  ```

- `react/tools/base.py`:
  - `build_tool_list()` - lines ~549-565 in original
  - `list_react_tool_names()` - lines ~567-582 in original

### 3.2 Move React Modules

**Files to Move**:
- `react_agent.py` → `react/agent.py`
- `react_tools.py` → DELETE (split into tools/)
- `streaming.py` → `react/streaming.py`
- `commands.py` → `react/commands.py` (or merge into agent.py)

### 3.3 Create react/__init__.py

```python
from .agent import RLMReActChatAgent, RLMReActChatSignature
from .streaming import ...
from .commands import COMMAND_DISPATCH, execute_command
from .tools import (
    build_tool_list,
    list_react_tool_names,
    load_document,
    # ... all tools
)
```

### 3.4 Update Root __init__.py

Replace direct imports with subpackage imports:
```python
# React system (re-exported)
from .react import (
    RLMReActChatAgent,
    RLMReActChatSignature,
    build_tool_list,
    list_react_tool_names,
    COMMAND_DISPATCH,
    execute_command,
    # ... all tools
)
```

**Testing**:
```bash
uv run pytest tests/unit/test_react_agent.py -v
# Expected: All 16 tests pass
```

**Verification**:
```python
python -c "
from fleet_rlm import RLMReactChatAgent, load_document, list_files
print('React imports OK')
"
```

---

## Phase 4: Stateful Layer (Day 3 Morning - 3 hours)
**Risk**: Low | **Dependencies**: Core layer

### 4.1 Create stateful/ Subpackage

**Files to Move**:
- `stateful_sandbox.py` → `stateful/sandbox.py`
- `agent_state.py` → `stateful/agent.py`

**Files to Create**:
- `stateful/__init__.py`:
  ```python
  from .sandbox import StatefulSandboxManager, ExecutionRecord, SandboxResult
  from .agent import AgentStateManager, AnalysisResult, CodeScript
  ```

### 4.2 Update Root __init__.py

```python
# Stateful execution (re-exported)
from .stateful import (
    StatefulSandboxManager,
  ExecutionRecord,
    SandboxResult,
    AgentStateManager,
    AnalysisResult,
    CodeScript,
)
```

**Testing**:
```bash
uv run pytest tests/unit/test_stateful_sandbox.py tests/unit/test_agent_state.py -v
```

---

## Phase 5: Runners Refactoring (Day 3 Afternoon - 5 hours)
**Risk**: High | **Dependencies**: Core, Stateful, React

### 5.1 Extract Common Runner Base

**Create `runners/base.py`**:
```python
"""Base utilities for all runners."""

def setup_runner(docs_path, env_file=None):
    """Common setup: read docs, check planner, create interpreter."""
    ...

def teardown_runner(interpreter):
    """Common teardown: shutdown interpreter."""
    ...

class RunnerContext:
    """Context manager for runner lifecycle."""
    ...
```

### 5.2 Create Individual Runner Files

**Create `runners/basic.py`**:
- `run_basic()` function
- `run_basic_parallel()` function

**Create `runners/architecture.py`**:
- `analyze_architecture()` function

**Create `runners/api.py`**:
- `generate_api_endpoints()` function

**Create `runners/errors.py`**:
- `analyze_errors()` function

**Create `runners/custom.py`**:
- `run_with_custom_tool()` function

**Create `runners/long_context.py`**:
- `analyze_long_document()` function (runner version)
- `summarize_long_document()` function (runner version)

**Create `runners/logs.py`**:
- `extract_from_logs()` function

**Create `runners/__init__.py`**:
```python
from .basic import run_basic, run_basic_parallel
from .architecture import analyze_architecture
from .api import generate_api_endpoints
from .errors import analyze_errors
from .custom import run_with_custom_tool
from .long_context import analyze_long_document, summarize_long_document
from .logs import extract_from_logs
```

### 5.3 Archive Original runners.py

**DO NOT DELETE** - Keep as reference:
- `runners.py` → `runners/legacy.py` (for reference during transition)

**After verification**, delete in cleanup phase.

### 5.4 Update All Imports

Update files that import from runners:
- `cli.py`: Change `from . import runners` → `from .runners import ...`
- `mcp/server.py`: Update import
- `server/routers/*.py`: Update imports

**Testing**:
```bash
# Test each runner individually
uv run pytest tests/unit -k runner -v

# Verify imports
python -c "from fleet_rlm import run_basic, analyze_architecture; print('OK')"
```

---

## Phase 6: CLI Restructuring (Day 4 Morning - 4 hours)
**Risk**: High | **Dependencies**: All previous phases

### 6.1 Create cli/commands/ Directory

**Create individual command files** (extract from cli.py):

- `cli/commands/__init__.py` - Export all command functions
- `cli/commands/code_chat.py` - `code_chat_cmd()` and helpers
- `cli/commands/run_basic.py` - `run_basic_cmd()`
- `cli/commands/serve_api.py` - `serve_api_cmd()`
- `cli/commands/serve_mcp.py` - `serve_mcp_cmd()`
- `cli/commands/init.py` - `init_cmd()`
- `cli/commands/check.py` - `check_secret_cmd()`
- `cli/utils.py` - Shared CLI helpers (table formatting, validation)

### 6.2 Create cli/main.py

**New slim main.py** (~100 lines):
```python
"""CLI entry point - Typer app definition."""
import typer
from .commands.code_chat import code_chat_cmd
from .commands.run_basic import run_basic_cmd
# ... other imports

app = typer.Typer()

app.command("code-chat")(code_chat_cmd)
app.command("run-basic")(run_basic_cmd)
# ... register all commands

if __name__ == "__main__":
    app()
```

### 6.3 Move and Archive Original cli.py

- `cli.py` → `cli/main.py` (new slim version)
- Keep backup: `cli/legacy.py` (original for reference)

### 6.4 Update Entry Points

**pyproject.toml** (if needed):
```toml
[project.scripts]
fleet-rlm = "fleet_rlm.cli.main:app"
```

**Testing**:
```bash
# Test CLI commands
uv run fleet-rlm --help
uv run fleet-rlm run-basic --help

# Run e2e tests
uv run pytest tests/e2e/test_cli_smoke.py -v
```

---

## Phase 7: Utils & Cleanup (Day 4 Afternoon - 3 hours)
**Risk**: Low | **Dependencies**: None

### 7.1 Create utils/ Subpackage

**Files to Move/Consolidate**:
- `rlm_helpers.py` → `utils/modal.py`
- `tools.py` (if not removed) → `utils/tools.py`
- `scaffold.py` → `utils/scaffold.py`

**Create `utils/__init__.py`** with re-exports

### 7.2 Remove Redundant Files

**Delete** (after verifying no imports):
- `tools.py` (68 lines - only regex_extract + re-exports)
- `commands.py` (optional - if merged into react/agent.py)

### 7.3 Final Cleanup

**Delete archived files**:
- `runners/legacy.py`
- `cli/legacy.py`

**Update Root __init__.py**:
- Remove redundant re-exports
- Ensure clean public API

---

## Phase 8: Final Verification (End of Day 4 - 2 hours)

### 8.1 Full Test Suite
```bash
# Unit tests
uv run pytest tests/unit -v --tb=short

# E2E tests (if applicable)
uv run pytest tests/e2e -v --tb=short

# Import verification
python -c "
import fleet_rlm
# Test all public exports
from fleet_rlm import (
    ModalInterpreter, RLMReActChatAgent, run_basic,
    configure_planner_from_env, AgentStateManager
)
print('All imports successful!')
"
```

### 8.2 Static Analysis
```bash
# Linting
uv run ruff check src

# Type checking
uv run ty check src

# Formatting
uv run ruff format --check src
```

### 8.3 Documentation Update
Update files referencing old paths:
- `AGENTS.md`
- `CLAUDE.md`
- `docs/` (if module references exist)

---

## Risk Mitigation & Rollback

### Per-Phase Rollback

Each phase is a single commit. If issues arise:
```bash
git revert HEAD  # Revert current phase
git checkout main  # Or go back to last known good state
```

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Import errors | Check `__init__.py` re-exports match original |
| Test failures | Likely import path issue - update test imports |
| Circular imports | Move shared code to `utils/common.py` |
| Missing exports | Add to root `__init__.py` |

### Emergency Procedures

**If main branch is broken**:
```bash
# Immediate rollback
git checkout main
git reset --hard <last-known-good-commit>
git push --force-with-lease  # If already pushed (careful!)
```

---

## Success Metrics

### Quantitative
- [ ] All 158+ unit tests pass
- [ ] Average file size <200 lines
- [ ] Zero files >500 lines
- [ ] No circular imports (verify with `pydeps`)
- [ ] Import time <100ms (verify with `python -X importtime`)

### Qualitative
- [ ] New dev can locate code in <30 seconds
- [ ] Adding CLI command requires editing 1 file (not 5)
- [ ] Unit testing any component requires <10 mocks
- [ ] Clear dependency direction: `cli` → `react` → `core`

---

## Timeline Summary

| Day | Phase | Duration | Risk |
|-----|-------|----------|------|
| 1 AM | 0. Preparation | 2h | Low |
| 1 PM | 1. Core Layer | 4h | Low |
| 2 AM | 2. Chunking/Signatures | 3h | Low |
| 2 PM | 3. React System | 4h | Medium |
| 3 AM | 4. Stateful Layer | 3h | Low |
| 3 PM | 5. Runners | 5h | High |
| 4 AM | 6. CLI | 4h | High |
| 4 PM | 7-8. Cleanup/Verify | 5h | Low |

**Total**: ~30 hours (3-4 days with breaks)

---

## Post-Implementation

### Code Review Checklist
- [ ] Verify all original exports still work
- [ ] Check test coverage hasn't decreased
- [ ] Review for any orphaned imports
- [ ] Validate documentation is accurate

### Team Communication
Update team on:
- New import patterns
- Where to add new code
- Deprecated patterns to avoid

### Future Enhancements (Out of Scope)
- Add `conftest.py` with shared fixtures
- Implement pytest markers for test categories
- Add module-level docstrings
- Create architecture decision record (ADR)

---

## Appendix: Import Mapping Reference

### Old → New Import Paths

| Old Import | New Import | Compatibility |
|------------|------------|---------------|
| `from fleet_rlm import ModalInterpreter` | Same | ✅ Backward compat |
| `from fleet_rlm import configure_planner_from_env` | Same | ✅ Backward compat |
| `from fleet_rlm.driver import sandbox_driver` | `from fleet_rlm.core import sandbox_driver` | ✅ Both work |
| `from fleet_rlm.react_agent import RLMReActChatAgent` | `from fleet_rlm.react import RLMReActChatAgent` | ✅ Both work |
| `from fleet_rlm.react_tools import load_document` | `from fleet_rlm.react.tools import load_document` | ✅ Both work |
| `from fleet_rlm import run_basic` | Same | ✅ Backward compat |
| `from fleet_rlm.runners import run_basic` | Same | ✅ New pattern |

**All old imports continue to work** through root `__init__.py` re-exports.
