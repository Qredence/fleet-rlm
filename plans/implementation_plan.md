# Maintainability: File Splitting Plan

Files exceeding ~600 LOC need splitting to stay maintainable.

| File                  | LOC | Action                                                       |
| --------------------- | --- | ------------------------------------------------------------ |
| `cli.py`              | 975 | Split into `cli.py` + `cli_demos.py`                         |
| `tools.py`            | 933 | Split into `tools.py` + `tools_sandbox.py`                   |
| `interpreter.py`      | 903 | **Accept** — single cohesive class                           |
| `runners.py`          | 797 | Split into `runners.py` + `runners_demos.py`                 |
| `test_react_agent.py` | 934 | Split into `test_react_agent.py` + `test_react_streaming.py` |
| `driver.py`           | 674 | **Accept** — close to limit                                  |
| `stateful/agent.py`   | 629 | **Accept** — close to limit                                  |

## Proposed Changes

---

### React Tools

#### [MODIFY] [tools.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/tools.py) (~570 LOC after)

Keep: shared helpers, `build_tool_list` shell, document tools (load/set/list), filesystem tools (list_files/read_file_slice/find_files), chunking tools, and the final tool assembly list.

#### [NEW] [tools_sandbox.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/tools_sandbox.py) (~360 LOC)

Extract: `parallel_semantic_map`, `analyze_long_document`, `summarize_long_document`, `extract_from_logs`, `rlm_query`, `edit_file`, `read_buffer`, `clear_buffer`, `save_buffer_to_volume`, `load_text_from_volume`.

Approach: Create a second builder function `build_sandbox_tools(agent)` that returns `list[dspy.Tool]`. The main `build_tool_list` calls it and extends.

---

### CLI

#### [MODIFY] [cli.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/cli.py) (~400 LOC after)

Keep: `app`, helpers (`_print_result`, `_handle_error`), `code_chat`, `run_react_chat`, `scaffold`, `health`, `inspect_sandbox`.

#### [NEW] [cli_demos.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/cli_demos.py) (~575 LOC)

Extract: `run_basic`, `run_architecture`, `run_api_endpoints`, `run_error_patterns`, `run_trajectory`, `run_custom_tool`, `run_long_context`. Register via `app.command()` from same `app` instance.

---

### Runners

#### [MODIFY] [runners.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/runners.py) (~250 LOC after)

Keep: shared helpers, `build_react_chat_agent`, `run_react_chat_once`, `arun_react_chat_once`.

#### [NEW] [runners_demos.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/runners_demos.py) (~550 LOC)

Extract: `run_basic`, `run_architecture`, `run_api_endpoints`, `run_error_patterns`, `run_trajectory`, `run_custom_tool`, `run_long_context`.

---

### Tests

#### [MODIFY] [test_react_agent.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_react_agent.py) (~550 LOC after)

Keep: fixtures, agent construction, tool registry, chat_turn, document management, reset, state export tests.

#### [NEW] [test_react_streaming.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/tests/unit/test_react_streaming.py) (~385 LOC)

Extract: All `test_*stream*` and `test_iter_chat_turn_stream*` tests (lines 177–345+).

---

### Not Split

- **`interpreter.py`** (903 LOC): Single `ModalInterpreter` class with tightly coupled methods (start/execute/stream/shutdown). Splitting would create artificial seams. Accept as-is.
- **`driver.py`** (674 LOC), **`stateful/agent.py`** (629 LOC): Borderline, accept.

## Verification Plan

### Automated Tests

```bash
ruff format . && ruff check .
pytest tests/unit/ -v --tb=short
```

### Import Checks

```bash
python -c "from fleet_rlm.react.tools import build_tool_list"
python -c "from fleet_rlm.react.tools_sandbox import build_sandbox_tools"
python -c "from fleet_rlm import cli"
python -c "from fleet_rlm import runners"
```
