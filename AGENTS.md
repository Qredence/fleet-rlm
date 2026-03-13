# Repository Guidelines

## Usage
Always use uv to run commands, not raw Python or pytest. This ensures the correct environment and dependencies are used.
Always use bun in src/frontend for frontend commands to run build and dev scripts

## Project Structure & Module Organization
Core Python code lives in `src/fleet_rlm/` (`core/`, `react/`, `server/`, `mcp/`, `analytics/`, `db/`).
Inside `src/fleet_rlm/core/`, keep host-side adapters (`interpreter.py`, `llm_tools.py`, `volume_ops.py`) conceptually separate from sandbox-side protocol/helpers (`driver.py`, `sandbox_tools.py`, `volume_tools.py`).
The websocket chat runtime now uses `RLMReActChatAgent` as the only top-level chat runtime. Deep symbolic work should happen through `rlm_query -> delegate_sub_agent.py -> child dspy.RLM`, while `docs_path` is preload-only and does not select a separate top-level execution mode.
Treat live conversation trace rendering as a backend/frontend contract spanning `src/fleet_rlm/server/routers/ws/*`, `src/fleet_rlm/react/streaming_context.py`, `src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts`, and `src/frontend/src/features/rlm-workspace/ChatMessageList.tsx`.
Use `src/fleet_rlm/utils/regex.py` for regex helpers.
Frontend code lives in `src/frontend/` (Vite + React + TypeScript).
Tests are organized by scope in `tests/unit/`, `tests/ui/`, `tests/integration/`, and `tests/e2e/`.
Operational scripts are in `scripts/`; DB migrations are in `migrations/`; API contract source is `openapi.yaml`; longer design/runbook docs are in `docs/`.
MLflow integration lives under `src/fleet_rlm/analytics/` and is additive to PostHog: use it for GenAI trace correlation, feedback, offline evaluation, and DSPy optimization workflows, not for general product telemetry.
The experimental Daytona-backed strict-RLM pilot lives in `src/fleet_rlm/daytona_rlm/`; treat it as an isolated reference runtime driven by the `fleet-rlm daytona-smoke` and `fleet-rlm daytona-rlm` CLI commands, not as a replacement for the current Modal/WebSocket chat stack. Its current contract is workspace-based and split into a guide-native interpreter core plus a thin host adapter. A Daytona run can bootstrap from an optional `repo_url`, optional staged local `context_paths`, both together, or neither for pure reasoning-only runs. The interpreter core owns persistent sandbox-side Python execution, sandbox-local LM boot, canonical `llm_query` / `llm_query_batched`, typed `SUBMIT`, sandbox-resident prompt objects for large task/observation payloads, recursive child sandbox spawning, tree-wide descendant cancellation, and direct workspace-local repo/context inspection. The host adapter owns result persistence, cancellation wiring, root synthesis safety validation, and UI event shaping. Daytona setup in this repo is strict: use `DAYTONA_API_KEY`, `DAYTONA_API_URL`, and optional `DAYTONA_TARGET`; `DAYTONA_API_BASE_URL` is considered a misconfiguration.
Within that Daytona pilot, workspace inspection should stay environment-native: `read_file_slice`, `grep_repo`, `chunk_text`, and `chunk_file` belong in the persistent sandbox runtime, prompt externalization belongs there too via `store_prompt`, `list_prompts`, and `read_prompt_slice`, `find_files` remains glob/path discovery, and `llm_query` / `llm_query_batched` are the canonical recursive builtins. `rlm_query`, `rlm_query_batched`, `FINAL`, and `FINAL_VAR` are compatibility aliases only. Local document and directory ingestion for Daytona should remain Daytona-native as well: host paths are resolved on the backend host, staged into `.fleet-rlm/context/`, and mirrored into child sandboxes so recursive calls see the same workspace inputs.
The Web UI now exposes this Daytona runtime through an explicit experimental runtime toggle in `RLM Workspace`. Keep `Modal chat` as the default product path, treat `execution_mode` as Modal-only, and only send Daytona-specific source controls (`repo_url`, `repo_ref`, `context_paths`, `max_depth`, and `batch_concurrency`) when `runtime_mode="daytona_pilot"`. In Daytona mode, keep the main chat surface visible while rendering the dedicated Daytona workbench inspector, a source-oriented setup card, a non-interactive Daytona RLM runtime indicator, live descendant node updates, warning surfaces, and tree-first inspection fed by structured run events instead of final-result-only hydration. The streamed Daytona runtime metadata should use `daytona_mode="recursive_rlm"`; do not overload `execution_mode` for Daytona payloads.

## Build, Test, and Development Commands
- `uv sync --all-extras --dev`: install Python dependencies for full local development.
- `uv run fleet web`: run the primary local Web UI experience.
- `uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]`: verify native Daytona setup, sandbox creation, repo clone, persistent driver state, and phase-aware diagnostics before using the pilot.
- `uv run fleet-rlm daytona-rlm [--repo <url>] [--context-path <path> ...] --task <text> [--max-depth N] [--batch-concurrency N]`: run the experimental Daytona-backed strict-RLM pilot against a Daytona workspace built from an optional repo, optional local context, or reasoning-only input.
- `make test-fast`: run default pytest suite (`not live_llm and not benchmark`).
- `make quality-gate`: run lint, format check, type check, tests, docs/metadata checks, and frontend checks.
- `make release-check`: full pre-release validation (quality + security + build + wheel checks).
- `make mlflow-server`: start the local OSS MLflow tracking server (`sqlite:///mlruns.db` on port `5000`).
- Frontend-only loop:
  - `cd src/frontend && bun install --frozen-lockfile`
  - `bun run dev` (local UI), `bun run check` (type/lint/tests/build/e2e)
- MLflow contributor workflows:
  - `uv run python scripts/export_mlflow_traces.py`
  - `uv run python scripts/evaluate_mlflow_traces.py`
  - `uv run python scripts/optimize_dspy_with_mlflow.py --dataset <json> --program <module:attr>`

## Coding Style & Naming Conventions
Use Python 3.10+, 4-space indentation, type hints on public functions, and clear docstrings for non-trivial logic.
Enforce style with:
- `uv run ruff format src tests`
- `uv run ruff check src tests`
- `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**" --exclude "src/fleet_rlm/analytics/**" --exclude "src/fleet_rlm/daytona_rlm/**"`

Naming: `snake_case` for modules/functions/tests, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Keep modules focused and avoid mixing unrelated concerns.

## Testing Guidelines
Use `pytest` with strict markers (`unit`, `ui`, `integration`, `e2e`, `live_llm`, `live_daytona`, `benchmark`).
Default local run: `uv run pytest -q -m "not live_llm and not live_daytona and not benchmark"`.
Name tests `test_<behavior>.py` and add regression tests for bug fixes.
Frontend tests use Vitest (`bun run test:unit`) and Playwright (`bun run test:e2e`).
For the Daytona pilot, prefer this focused validation set before broader gates:
- `uv run pytest -q tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py`
- `uv run ruff check src/fleet_rlm/daytona_rlm src/fleet_rlm/cli.py tests/unit/test_daytona_rlm_config.py tests/unit/test_daytona_rlm_smoke.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py`
- `DAYTONA_LIVE_TESTS=1 uv run pytest -q tests/integration/test_daytona_smoke_live.py` for the opt-in real Daytona validation lane
Use the Daytona flow in this order: configure `DAYTONA_API_KEY` + `DAYTONA_API_URL`, run `daytona-smoke`, then run `daytona-rlm` only if the smoke diagnostics are clean.
For chat-runtime or trace changes, prefer this focused validation set before broader gates:
- `uv run pytest -q tests/ui/server/test_server_config.py tests/ui/ws/test_chat_stream.py tests/unit/test_tools_sandbox.py`
- `uv run pytest -q tests/unit/test_mlflow_integration.py tests/ui/server/test_api_contract_routes.py`
- `cd src/frontend && bun run type-check`
- `cd src/frontend && bun run test:unit src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx`
For Daytona Web UI runtime-toggle changes, prefer this focused validation set before broader gates:
- `uv run pytest -q tests/unit/test_daytona_rlm_driver.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_ws_chat_helpers.py tests/ui/ws/test_chat_stream.py`
- `uv run ruff check src/fleet_rlm/daytona_rlm src/fleet_rlm/server/routers/ws src/fleet_rlm/server/schemas tests/unit/test_ws_chat_helpers.py tests/ui/ws/test_chat_stream.py`
- `cd src/frontend && bun run test:unit src/features/rlm-workspace/daytona-workbench/__tests__/daytonaWorkbenchAdapter.test.ts src/features/rlm-workspace/daytona-workbench/__tests__/DaytonaWorkbench.test.tsx src/features/rlm-workspace/__tests__/DaytonaSetupCard.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx src/stores/__tests__/chatStore.test.ts src/components/chat/__tests__/ChatInput.test.tsx src/components/chat/input/__tests__/RuntimeModeDropdown.test.tsx`
- `cd src/frontend && bun run type-check`

## Commit & Pull Request Guidelines
Follow Conventional Commits seen in history: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:` (scopes encouraged, e.g., `fix(frontend): ...`).
Before opening a PR, run at least `make quality-gate`.
PRs should include: clear summary, linked issue (`Fixes #123`), type of change, commands run + results, and screenshots/GIFs for UI updates. Update docs (`README.md`, `AGENTS.md`, `docs/`) when behavior changes.

## Security & Configuration Tips
Use `.env.example` as a template; never commit `.env` or secrets. Keep API keys in environment variables/secret managers, not source code. Treat `live_llm` and benchmark tests as opt-in and run them intentionally.
