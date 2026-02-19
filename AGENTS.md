# AGENTS.md

Guidance for contributors working in this repository.

## Project Overview

`fleet-rlm` is a Python package for Recursive Language Models (RLM) using DSPy + Modal for secure long-context code execution.

Reference: <https://arxiv.org/abs/2501.123>

## Setup

```bash
# from repo root
uv sync --extra dev
uv sync --extra dev --extra server
uv sync --extra dev --extra mcp
cp .env.example .env
```

## Modal Setup

```bash
# from repo root
uv run modal setup
uv run modal volume create rlm-volume-dspy
uv run modal secret create LITELLM \
  DSPY_LM_MODEL=... \
  DSPY_LM_API_BASE=... \
  DSPY_LLM_API_KEY=...

# serve-api defaults to persistent volume `rlm-volume-dspy`
# when interpreter.volume_name is not explicitly provided.
```

## Common Commands

```bash
# from repo root
uv run fleet-rlm --help
uv run fleet-rlm run-basic --question "What are the first 12 Fibonacci numbers?"
uv run fleet-rlm run-architecture --docs-path rlm_content/dspy-knowledge/dspy-doc.txt --query "Extract all modules and optimizers"
uv run fleet-rlm code-chat --opentui
uv run fleet-rlm serve-api --port 8000
uv run fleet-rlm serve-api interpreter.volume_name=my-volume --port 8000
uv run fleet-rlm serve-mcp --transport stdio
uv run python scripts/db_init.py
uv run alembic upgrade head
uv run python scripts/dev_issue_token.py --tid <tid> --oid <oid> --email dev@example.com --name "Dev User"
uv run python scripts/db_smoke.py

# Quality gate (run all four before pushing)
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src
uv run pytest -q

# Individual checks
uv run ruff check src tests
uv run ruff format --check src tests
uv run ruff format src tests
uv run ty check src
uv run pytest

# Performance baseline workflow (credential-gated)
uv run python scripts/perf/compare_baseline.py --update-baseline --baseline scripts/perf/baseline/rlm_benchmarks_baseline.json
uv run python scripts/perf/compare_baseline.py --baseline scripts/perf/baseline/rlm_benchmarks_baseline.json --threshold 0.20
```

## Interactive Surface

- OpenTUI under `tui/` and Ink TUI under `tui-ink/` are the supported interactive runtimes.
- Python Textual and legacy prompt-toolkit UI runtimes have been removed (v0.4.0).
- TUI keyboard interactions are centralized through shared shortcut/focus plumbing (global + pane-specific shortcuts) instead of ad-hoc handlers per component.
- `src/fleet_rlm/models.py` contains streaming data models (`StreamEvent`, `TurnState`) used by `react/streaming.py`, not UI code.

## Architecture Highlights

### Config & Core

- `src/fleet_rlm/config.py`: top-level Hydra `AppConfig` loader and runtime settings
- `src/fleet_rlm/conf/`: Hydra config YAML directory
- `src/fleet_rlm/core/config.py`: env loading + planner LM configuration
- `src/fleet_rlm/core/interpreter.py`: `ModalInterpreter` lifecycle + JSON bridge + execution profiles (`ROOT_INTERLOCUTOR`, `RLM_DELEGATE`, `MAINTENANCE`)
- `src/fleet_rlm/core/driver.py`: sandbox-side execution driver and main protocol loop
- `src/fleet_rlm/core/driver_factories.py`: sandbox helper factories (`SUBMIT`, `llm_query`, tool registration)
- `src/fleet_rlm/core/sandbox_tools.py`: sandbox-side buffer/chunking/grep helpers
- `src/fleet_rlm/core/volume_tools.py`: sandbox-side volume read/write helpers
- `src/fleet_rlm/core/volume_ops.py`: host-side volume operations
- `src/fleet_rlm/core/llm_tools.py`: host-side LLM query helpers
- `src/fleet_rlm/core/session_history.py`: sandbox-side execution history tracking
- `src/fleet_rlm/core/output_utils.py`: output formatting utilities
- `src/fleet_rlm/logging.py`: structured logging helper

### ReAct Agent & Tools

- `src/fleet_rlm/react/agent.py`: `RLMReActChatAgent` (`dspy.Module` subclass) — uses mixins and `__getattr__` delegation
- `src/fleet_rlm/react/core_memory.py`: `CoreMemoryMixin` (persona/human/scratchpad memory)
- `src/fleet_rlm/react/document_cache.py`: `DocumentCacheMixin` (document storage and alias management)
- `src/fleet_rlm/react/validation.py`: response guardrail validation
- `src/fleet_rlm/react/tool_delegation.py`: dynamic `__getattr__` tool dispatch (replaces 25+ boilerplate methods)
- `src/fleet_rlm/react/tools.py`: ReAct tool assembly and host-side tool definitions
- `src/fleet_rlm/react/document_tools.py`: document loading/reading tools
- `src/fleet_rlm/react/filesystem_tools.py`: file listing/search tools
- `src/fleet_rlm/react/chunking_tools.py`: text chunking tools
- `src/fleet_rlm/react/tools_sandbox.py`: sandbox-specific tools (`rlm_query`, `edit_file`) with depth enforcement
- `src/fleet_rlm/react/tools_sandbox_helpers.py`: shared sandbox tool helpers
- `src/fleet_rlm/react/delegate_sub_agent.py`: `spawn_delegate_sub_agent()` — shared true-recursion helper
- `src/fleet_rlm/react/tools_rlm_delegate.py`: RLM delegate tools (all use true recursive sub-agents)
- `src/fleet_rlm/react/tools_memory_intelligence.py`: memory intelligence tools (tree, audit, migration, clarification)
- `src/fleet_rlm/react/runtime_factory.py`: lazy-loading runtime module factory
- `src/fleet_rlm/react/rlm_runtime_modules.py`: canonical reusable DSPy runtime wrappers for long-context tasks
- `src/fleet_rlm/react/streaming.py`: async/streaming ReAct execution with trajectory normalization
- `src/fleet_rlm/react/commands.py`: WebSocket command dispatch → tool mapping

### Surfaces

- `src/fleet_rlm/cli.py`: Typer CLI entrypoint
- `src/fleet_rlm/cli_commands/`: CLI subcommand modules (`init_cmd.py`, `serve_cmds.py`)
- `src/fleet_rlm/terminal/`: terminal chat helpers (`commands.py`, `settings.py`, `ui.py`)
- `src/fleet_rlm/runners.py`: high-level task runners
- `src/fleet_rlm/server/`: optional FastAPI server (`/ws/chat`, `/ws/execution`, `/chat`, `/tasks/basic`, `/auth/me`)
- `src/fleet_rlm/mcp/`: optional FastMCP server
- `src/fleet_rlm/bridge/`: stdio JSON-RPC bridge for Ink TUI
- `src/fleet_rlm/stateful/`: stateful agent and sandbox models

## Testing Notes

Tests mock Modal APIs and should run without cloud credentials.

- `tests/e2e/test_cli_smoke.py`
- `tests/integration/test_rlm_integration.py`
- `tests/unit/test_driver_protocol.py`, `test_driver_helpers.py`, `test_llm_query_mock.py`
- `tests/unit/test_config.py`
- `tests/unit/test_react_agent.py`, `test_react_tools.py`, `test_react_streaming.py`
- `tests/unit/test_tools_sandbox.py`, `test_tools.py`, `test_memory_tools.py`
- `tests/unit/test_context_manager.py`
- `tests/unit/test_terminal_chat_helpers.py`
- `tests/unit/test_bridge_handlers.py`, `test_bridge_protocol_server.py`
- `tests/ui/server/test_router_*.py`, `test_server_*.py`

## Conventions

- Python 3.10+
- Type-check with `ty` (not `mypy`)
- Format/lint with `ruff`
- Prefer `uv run ...` for commands
- `serve-api` defaults to persistent Modal volume `rlm-volume-dspy` when no `interpreter.volume_name` is provided
- ReAct document tools (`load_document`, `read_file_slice`) support PDF ingestion via MarkItDown with pypdf fallback; scanned/image-only PDFs require OCR before analysis
- Neon/Postgres is the canonical multi-tenant app state store for API runtime state (`runs`, `run_steps`, `artifacts`, `memory_items`, `jobs`, `skill_taxonomies`, `taxonomy_terms`, `skills`, `skill_versions`, `skill_term_links`, `run_skill_usages`, etc.)
- Tenant isolation uses Postgres RLS with transaction-local tenant context via `set_config('app.tenant_id', ..., true)` in repository methods
- Server auth defaults to `AUTH_MODE=dev` and supports debug headers or local HS256 bearer JWT; WebSocket clients may also use dev query auth (`debug_tenant_id`, `debug_user_id`, optional `debug_email`, `debug_name`, or `access_token`)
- `AUTH_REQUIRED` controls route enforcement in dev (`false` by default for local iteration, `true` for strict enforcement); `entra` remains fail-closed until JWKS verification wiring is implemented
- `AUTH_MODE=entra` is scaffolded and fail-closed until JWKS verification wiring is implemented
- ReAct long-context delegate tools (`analyze_long_document`, `summarize_long_document`, `extract_from_logs`, etc.) use true recursive sub-agents via `spawn_delegate_sub_agent()` — each spawns a new `RLMReActChatAgent` at `depth + 1` with full tool access
- Additive signature tools are available for advanced workflows:
  - `grounded_answer` (returns structured citations with keys `source`, `chunk_id`, `evidence`, `reason`)
  - `triage_incident_logs`
  - `plan_code_change`
  - `propose_core_memory_update`
- Memory/volume enrichment tools are available for management workflows:
  - `memory_tree` (bounded recursive tree for Modal volume paths)
  - `memory_action_intent` (risk + confirmation-oriented action planning)
  - `memory_structure_audit` and `memory_structure_migration_plan` (non-mutating structure governance)
  - `clarification_questions` (safe clarification generation for ambiguous/high-risk operations)
- WebSocket interactive chat should carry identity envelope fields (`workspace_id`, `user_id`, `session_id`) so per-user/per-workspace state can be restored
- `/ws/chat` is the primary interactive path; keep ReAct as the user-facing orchestrator and delegate heavy tool execution through recursive sub-agents
- `/ws/execution` is a dedicated filtered execution stream for Artifact Canvas consumers; clients must subscribe with matching `workspace_id`, `user_id`, and `session_id` query params
- Execution observability is additive: preserve `/ws/chat` envelope compatibility (`{"type":"event","data":...}`) while emitting structured `execution_started` / `execution_step` / `execution_completed` events on `/ws/execution`
- Keep WebSocket auth/runtime documentation synchronized with implementation whenever auth flow behavior changes (`AUTH_MODE`, `AUTH_REQUIRED`, debug identity, and bearer token paths).
- Session state manifests (logs/memory/docs/artifacts/metadata) are persisted under Modal Volume V2 paths rooted at `/data/workspaces/<workspace_id>/users/<user_id>/`

## Import Verification

- Always verify imports after any file creation or refactoring. Run `uv run python -c "import <module>"` to catch ImportErrors immediately.

## Code Quality and Debugging

- When fixing type/lint errors, first clear stale caches (`.ruff_cache/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`) and run `pre-commit clean` before making code changes.

## Task Planning

- Before creating tasks or making extensive changes, confirm the user's intent - especially for 'replan' or 'start fresh' requests.

## Modal Sandbox

- For Modal Sandbox work: always verify volume paths exist and API credentials are valid before running tests.

## Multi-Agent Workflows

- When using the teammate/RLM system: prefer using existing agents in `@.claude/agents/` rather than spawning new exploration tasks.
