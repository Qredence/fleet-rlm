# Replacement-readiness TODOS (`dev-0.7`)

Executable mission checklist derived from [`IMPLEMENTATION_PHASES.md`](IMPLEMENTATION_PHASES.md).
Narrative detail stays in that document; **this file is the work queue**.

## How to use

- Run **one mission at a time** (`create seam → preserve behavior → migrate one concern → validate → repeat`).
- Claim a mission by setting `Status: in_progress` before editing code.
- Mark `Status: done` only after the validation gate and acceptance checkboxes pass.
- Do not bundle multiple missions in one PR/session.
- Public HTTP routes, SSE framing, durable Result shapes, Workspace semantics, DB semantics, wheel contents, and TUI behavior stay stable unless the mission explicitly fixes a proven bug.

**Status values:** `open` | `in_progress` | `done`

**Tier values:**

| Tier | Meaning |
|------|---------|
| `blocker` | Required before default-branch replacement (missions 01–06) |
| `recommended` | Complete for a long-lived canonical codebase (missions 07–13) |
| `rc` | Freeze + full certification (mission 14) |
| `cutover` | Post-rebase GitHub protection (mission 15) |
| `defer` | Explicitly after replacement — do not pull into this program |

## Baseline

- **Branch:** `dev-0.7`
- **Audit SHA:** `c34e7d84d8dd753e94d08dc987fef686f1f65e62` (matches `IMPLEMENTATION_PHASES.md`)
- **Verified against:** `src/fleet_rlm/` and `tools/fleet-tui/` (static audit; Mission 04 live two-child batch canary executed; Mission 14 remains the full RC live gate)

### Corrections vs `IMPLEMENTATION_PHASES.md`

| Topic | Use this instead of the phase-doc wording |
|-------|-------------------------------------------|
| Nested ternaries (M01) | Also rewrite skill `phase` nesting in `projection.ts` (~542), not only prior RLM content and `/profiles` |
| Python checker tests (M01) | **ADD** `tests/unit/scripts/test_check_codebase_tree.py` — no checker test exists today |
| Memory categories (M02) | Categories are open regex-validated strings — **no** `MEMORY_CANDIDATE_CATEGORIES`. Use free-form / list policy editor, not `multi_choice` |
| Trajectory tests (M07) | `tests/unit/backend/rlm/test_trajectory_projection.py` **already exists** and imports helpers from `runner.py` — rewire after extract |
| Inventory rebuild (M13) | Also fix `composition/testing.py`, not only `composition/daytona.py` |
| TUI AGENTS ownership (M12) | `tools/fleet-tui/AGENTS.md` wrongly attributes live/durable projection to `transcript.ts`; projection lives in `projection.ts` |

### Mission dependency map

```text
M01 → M02 → M03 → M04 → M05 → M06 → M07 → M08 → M09 → M10 → M11 → M12 → M13 → M14 → M15
```

---

## Blockers (P0–P1) — missions 01–06

### Mission 01 — Enforce clarity rules (no nested ternaries)

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** —
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 1

**Purpose:** Make nested-ternary clarity mechanically enforceable in TUI Biome and backend Python AST checks so later refactors cannot reintroduce it.

**Non-goals:**

- Ban ordinary single ternaries
- Enable a formatter/rule that rewrites simple `if` into ternaries
- Create a separate `check_nested_ternaries.py`

**Compatibility:** No public API/SSE/TUI behavior change; rewrite is expression → explicit branches only.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `tools/fleet-tui/biome.json` — enable `style.noNestedTernary: "error"` |
| EDIT | `tools/fleet-tui/src/tui/projection.ts` — prior RLM content, tool status, skill phase |
| EDIT | `tools/fleet-tui/src/tui/commands.ts` — `/profiles` status |
| EDIT | `tools/fleet-tui/src/tui/autocomplete.ts` — resume/skill completions (extra Biome site) |
| EDIT | `tools/fleet-tui/src/tui/command-presenter.ts` — profile/settings nested ternaries (extra sites) |
| EDIT | `src/fleet_rlm/files/memory_models.py` — nested `record_version` IfExp → helper |
| EDIT | `src/fleet_rlm/rlm/dspy_contract.py` — nested history selection IfExp |
| EDIT | `scripts/check_codebase_tree.py` — detect nested `ast.IfExp` |
| ADD | `tests/unit/scripts/test_check_codebase_tree.py` |

**Implementation notes:**

- Biome: keep `recommended: true` and existing `style.noNonNullAssertion: "off"`.
- Python: flag an `ast.IfExp` whose `body` or `orelse` contains another `ast.IfExp`; leave simple conditional expressions allowed.
- Extend the existing import-boundary checker; do not add a new repo script.

**Tests:**

- Nested-`IfExp` fixture fails the checker; simple `IfExp` still passes
- Existing import-boundary checks still pass
- TUI projection / command tests still pass

**Validation gate:**

```bash
cd tools/fleet-tui && pnpm lint
cd tools/fleet-tui && pnpm exec biome check src/tui/projection.ts src/tui/commands.ts src/tui/autocomplete.ts src/tui/command-presenter.ts
uv run python scripts/check_codebase_tree.py
uv run pytest tests/unit/scripts/test_check_codebase_tree.py -q
cd tools/fleet-tui && pnpm exec vitest run src/tui/tests/projection.test.ts
```

**Acceptance:**

- [x] Biome reports nested ternaries as errors and current sites are rewritten
- [x] Python checker rejects nested `IfExp` and allows simple ternary
- [x] Existing tests pass

---

### Mission 02 — Make configuration policy one coherent contract

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** Mission 01
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 2

**Purpose:** Close runtime/config drift: expose `rlm.autonomous_memory_categories` on the editable policy surface and prevent future inventory omissions with invariant tests; dedupe root policy-document parsing.

**Non-goals:**

- Generic `SettingSpec` framework that generates Pydantic + TOML + API + editors
- Closed category vocabulary / `multi_choice` against a fixed enum
- Changes to `Settings` public runtime behavior or secret exposure

**Compatibility:** Profiles load identically; unknown TOML fields still fail closed; secrets remain unavailable via policy API.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `src/fleet_rlm/config.py` — `PolicyDocument` / `_read_policy_document` / `_policy_document_from_mapping` |
| EDIT | `src/fleet_rlm/config_policy.py` — `string_list` editor + `rlm.autonomous_memory_categories` field |
| EDIT | `src/fleet_rlm/api/schemas.py` — editor Literal includes `string_list` |
| REGEN | `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` |
| EDIT | `tools/fleet-tui/src/tui/command-presenter.ts` — string_list editing |
| EDIT | `tests/unit/backend/test_config_policy.py` — editable + inventory tests |

**Implementation notes:**

- Categories remain open regex-validated strings via `normalize_memory_candidate_categories` — editor is `string_list`, not closed `multi_choice`.
- Inventory invariant: every PolicyField path is TOML-schema-valid; every flattened non-`*_env` Settings key has a `settings_field` PolicyField entry.

**Tests:**

- Profiles load; default profile resolution unchanged
- Autonomous memory categories appear and edit correctly through policy API
- Inventory invariant tests fail if a field drifts
- Secrets still absent from policy responses

**Validation gate:**

```bash
make api-check
uv run pytest tests/unit/backend/test_config.py tests/unit/backend/test_config_policy.py -q
```

**Acceptance:**

- [x] Field visible/editable on policy surface
- [x] Inventory invariant tests land and pass
- [x] `PolicyDocument` helper removes duplicated root parsing without behavior change
- [x] No secret leakage; no `Settings` public behavior change

---

### Mission 03 — Bound Workspace Agent provider execution

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** Mission 02
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 3

**Purpose:** Give `run_workspace_agent` / `run_workspace_agent_async` an explicit Daytona `code_run` timeout so post-commit ownership cannot retain leases indefinitely on a stalled provider call.

**Non-goals:**

- New public / TOML timeout knob
- Detach-and-forget post-commit threads
- Release Sandbox while the promotion thread still uses it
- Rewrite Workspace Agent security algorithm

**Compatibility:** Ownership ordering unchanged: short visible wait → owned promotion → bounded provider call → `PreparedRun.aclose()` → lease release exactly once.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `src/fleet_rlm/daytona/workspace_agent.py` — `run_workspace_agent` / async (~1267–1277) pass `timeout=` |
| EDIT | `src/fleet_rlm/daytona/workspace_fs.py` — only if needed for timeout injection |
| EDIT | `src/fleet_rlm/daytona/workspace_memory.py` — only if needed |
| EDIT | `src/fleet_rlm/daytona/run_environment.py` — only if needed |

**Implementation notes:**

- Prefer reuse of an existing runtime bound (e.g. `rlm_execution_timeout_s` / interpreter timeout semantics) **or** one internal constant — not a new settings field.
- Interface shape: `timeout_s: float` keyword on host runners.
- Hostile fake: `process.code_run` never completes normally must still settle.

**Tests:**

- Workspace Agent contract tests
- Workspace Memory promotion / post-commit ownership tests
- Run preparation/cleanup tests proving `PreparedRun.aclose()` returns and lease releases once

**Validation gate:**

```bash
uv run pytest tests/unit/backend/daytona/test_workspace_agent_stat.py \
  tests/unit/backend/daytona/test_workspace_agent_delete_patch.py -q
# plus the promotion / preparation tests touched by this mission
```

**Acceptance:**

- [x] Provider call is bounded
- [x] Public post-commit wait remains short
- [x] Hostile hang eventually times out; resources release once
- [x] No new TOML/public setting

---

### Mission 04 — Live two-child `rlm_query_batched` certification

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** Mission 03
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 4

**Purpose:** Live-certify Root `rlm_query_batched([prompt_a, prompt_b])` with real DSPy + real Daytona + two simultaneous child RLMs + cleanup.

**Non-goals:**

- 8-child stress test
- New concurrency config
- Arbitrary recursive depth beyond `RLM_NATIVE_CHILD_DEPTH = 1`
- Enlarging the single-child live test into a kitchen-sink suite

**Compatibility:** Deterministic recursive unit behavior unchanged; this adds a live canary only.

**Files:**

| Action | Path |
|--------|------|
| ADD | `tests/live/backend/test_daytona_recursive_batch.py` |
| EDIT | (optional) live helpers only if shared fixtures are required |

**Implementation notes:**

- Mirror patterns from `tests/live/backend/test_phase2_daytona_recursive.py`.
- Assert: distinct child Sandbox IDs and Volume paths; both started/completed; `peak_child_concurrency == 2`; ordered results for `[prompt_a, prompt_b]`; Root synthesis + SUBMIT; all child Sandboxes removed; admission permits returned; no active child lease; child fan-out not exposed recursively.
- Live lane requires explicit `FLEET_LIVE=1` and credentialed env; do not fold into default `make check`.

**Tests:** The new live module itself is the gate.

**Validation gate:**

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_daytona_recursive_batch.py -q
```

**Acceptance:**

- [x] Two-child live canary green
- [x] Cleanup invariants proven
- [x] No new runtime knobs

---

### Mission 05 — Align package support, dependency policy, and CI

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** Mission 04
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 5

**Purpose:** Make declared Python/DSPy support match what CI actually certifies.

**Non-goals:**

- Exact-pin the project to `dspy==3.3.0` solely to match a stale workflow name
- Collapse CircleCI quality/unit/e2e/daytona/TUI job structure
- Drop Python 3.11 from `requires-python` without removing classifiers

**Compatibility:** Runtime `dspy>=3.3.0,<3.4` patch-range policy stays; full gate remains Python 3.13.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `pyproject.toml` — remove dead `tomli>=2.4.1; python_version < '3.11'` |
| EDIT | `.github/workflows/check-dspy-pin.yml` — verify **locked** DSPy version, not hard-coded `dspy==3.3.0` installability framing |
| EDIT | `.circleci/config.yml` (and/or companion workflows) — add lightweight 3.11 + 3.12 compat jobs |
| EDIT | release/preflight docs or scripts only if they contradict the new lanes |

**Implementation notes:**

- Package already declares `requires-python = ">=3.11,<3.14"` and `dspy>=3.3.0,<3.4`.
- Compat jobs: lock/install + import/package validation + unit/contract — not full Daytona/E2E.
- Keep 3.13 as the full gate image.

**Tests / Validation gate:**

```bash
# local smoke for metadata + unit; CI must show 3.11/3.12/3.13 lanes green
uv sync --all-extras --dev
uv run pytest tests/unit/backend tests/contracts/backend -q
```

**Acceptance:**

- [x] Dead `tomli` marker removed
- [x] DSPy workflow matches locked version / patch-range policy
- [x] Lightweight 3.11 and 3.12 jobs exist and pass
- [x] 3.13 full gate unchanged in role

---

### Mission 06 — Give Turn preparation one explicit owner

- **Tier:** `blocker`
- **Status:** `done`
- **Depends on:** Mission 05
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 6

**Purpose:** Flatten `TurnCoordinator.open()` ownership without changing the Run state machine: shared claim/cleanup helpers stop being private cross-imports; preparation gets one object for task/claim-loss/cancel/settle.

**Non-goals:**

- Polymorphic attempt hierarchy / framework
- Merging `TurnCoordinator`, `RunExecutionDriver`, and `RunLifecycle` into one type
- Changing claim/heartbeat/quarantine semantics

**Compatibility:** Existing preparation failure modes remain covered (claim loss, cancel, timeout, late provider completion, disconnect, cleanup failure, successful handoff).

**Files:**

| Action | Path |
|--------|------|
| EDIT | `src/fleet_rlm/chat/turn_coordinator.py` — `open()` (~152–364); stop `_private` imports from `run_execution` (~15–23) |
| EDIT | `src/fleet_rlm/chat/run_execution.py` — export or move shared helpers (`_ClaimHeartbeat`, `_shield_cleanup`, etc.) |
| ADD | `src/fleet_rlm/chat/run_ownership.py` — **only if** it materially simplifies callers |
| ADD | `src/fleet_rlm/chat/preparation_attempt.py` — **only if** it materially simplifies callers |

**Implementation notes:**

- `PreparationAttempt`-shaped API: `wait() -> PreparedRun`, `cancel_and_settle()`.
- Move only genuinely shared prep+execution ownership helpers.
- Reviewer should understand `open()` from top-level branches, not five nested closures.

**Tests:** Existing TurnCoordinator / run execution / preparation behavior tests (claim loss, cancel, timeout, cleanup failure, handoff, heartbeat stop).

**Validation gate:**

```bash
uv run pytest tests/unit/backend -q -k 'turn_coordinator or run_execution or preparation or claim'
# narrow to the concrete modules this mission touches after locating them
```

**Acceptance:**

- [x] No private sibling-module imports for ownership helpers
- [x] Preparation attempt ownership is explicit
- [x] Behavior tests green; Run state machine unchanged

---

## Recommended (P2–P5) — missions 07–13

### Mission 07 — Extract pure trajectory reconciliation from `RLMRunner`

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 06
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 7

**Purpose:** Let `runner.py` read as execution orchestration; move pure trajectory/live-detail reconciliation into a dedicated module; tighten Memory candidate drain typing.

**Non-goals:**

- `TrajectoryService` class / polymorphic projection engine
- Changing emitted RuntimeEvents or durable ExecutionDetails semantics
- Legacy `getattr` / dynamic drain compatibility if every adapter already implements the protocol

**Compatibility:** Identical trajectory + live observations → identical RuntimeEvents, ExecutionDetails, stream IDs, and correction behavior.

**Files:**

| Action | Path |
|--------|------|
| ADD | `src/fleet_rlm/rlm/trajectory_projection.py` |
| EDIT | `src/fleet_rlm/rlm/runner.py` — move helpers `_trajectory_details` (~279+), `_preserve_stream_id`, `_stream_text`, `_align_trajectory_detail`, `_same_stream_payload`, `_detail_position`, `_outside_reasoning_position`, `_trajectory_insertion`, `_reconcile_trajectory` |
| EDIT | `src/fleet_rlm/rlm/context.py` — `PreparedCapabilities.drain_memory_candidates` → `tuple[MemoryCandidate, ...]` |
| EDIT | `src/fleet_rlm/chat/capability_preparation.py` / Daytona prepared capabilities as needed |
| EDIT | `tests/unit/backend/rlm/test_trajectory_projection.py` — **rewire imports** (file already exists) |

**Implementation notes:**

- Prefer plain functions with domain names.
- Replace `_drain_memory_candidates` dynamic path in `runner.py` with direct typed drain.

**Validation gate:**

```bash
uv run pytest tests/unit/backend/rlm/test_trajectory_projection.py -q
uv run pytest tests/unit/backend/rlm -q
```

**Acceptance:**

- [x] Helpers live in `trajectory_projection.py`
- [x] Existing trajectory tests rewired and green
- [x] `drain_memory_candidates` typed; no dynamic legacy path

---

### Mission 08 — Separate batched scheduling from recursive child semantics

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 07
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 8

**Purpose:** Move bounded ThreadPool batch scheduling out of `recursive_calls.py` so `RecursiveRLMExecutor` owns recursive semantics only.

**Non-goals:**

- Extensible generic scheduler abstraction
- Changing public `tool` / `batched_tool` contracts
- Raising native child depth

**Compatibility:** Preserve atomic reservation, order, max parallelism, first failure, timeout, queued cancellation, running lease retention, worker-local spans, cleanup error propagation.

**Files:**

| Action | Path |
|--------|------|
| ADD | `src/fleet_rlm/rlm/recursive_batch.py` |
| EDIT | `src/fleet_rlm/rlm/recursive_calls.py` — keep policy, single-child, depth, child construction/cleanup, tools, metrics; move `_call_batched` scheduling (~788+) |
| EDIT | `tests/unit/backend/rlm/test_recursive_calls.py` — update imports if needed; keep `test_recursive_batch_*` coverage |

**Implementation notes:**

- Narrow API e.g. `run_reserved_batch(reservations, *, execute, deadline, max_parallel, ...) -> list[str]`.
- If internal `call_count` means reserved calls, rename to `reserved_call_count` (or equivalent) for clarity.

**Validation gate:**

```bash
uv run pytest tests/unit/backend/rlm/test_recursive_calls.py -q
```

**Acceptance:**

- [x] Batch scheduling isolated in `recursive_batch.py`
- [x] All current batch tests green
- [x] Ambiguous counter naming cleaned up if present

---

### Mission 09 — Extract DSPy synchronous Daytona bridge

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 08
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 9

**Purpose:** Make `interpreter.py` describe the Fleet interpreter; move sync/async transport bridge to `dspy_sync_bridge.py`.

**Non-goals:**

- Daytona synchronous client stack
- `RLM.acall()` / native async interpreter migration
- Worker-thread removal

**Compatibility:** DSPy still sees a sync interpreter view over AsyncSandbox; Tool/SUBMIT/observation semantics unchanged.

**Files:**

| Action | Path |
|--------|------|
| ADD | `src/fleet_rlm/daytona/dspy_sync_bridge.py` |
| EDIT | `src/fleet_rlm/daytona/interpreter.py` — move `_SyncBridgeLoop` (~310+), `_sync_await`, `_SyncCodeInterpreter`, `_SyncProcess`, `_SyncFileSystem`, `_SyncDaytonaSandbox` (~473+), `sync_sandbox`, `bridge_service_loop`, `set_bridge_service_loop` |

**Implementation notes:**

- Rename `_SyncDaytonaSandbox` → `_DSPySyncSandboxView`.
- Keep in `interpreter.py`: `DaytonaCodeInterpreter`, broker wiring, host Tool mediation, SUBMIT, observations, repair feedback, DSPy `CodeInterpreter` contract.

**Validation gate:**

```bash
uv run pytest tests/unit/backend/daytona -q
uv run python scripts/check_codebase_tree.py
```

**Acceptance:**

- [x] Bridge symbols live in `dspy_sync_bridge.py`
- [x] Interpreter file owns Fleet interpreter semantics
- [x] No async-DSPy migration sneak-in

---

### Mission 10 — Workspace Agent as real Python source

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 09
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 10

**Purpose:** Extract the remote Workspace Agent program from quoted string lines into ordinary packaged Python source without changing security or wire protocol.

**Non-goals:**

- Rewriting CAS / lock / path / fsync / delete algorithms while extracting
- Importing the runtime module as host behavior
- `inspect.getsource()` or a second template language

**Compatibility:** Provider round trips, wire payload, response shape, error mapping, and security behavior unchanged.

**Files:**

| Action | Path |
|--------|------|
| ADD | `src/fleet_rlm/daytona/workspace_agent_runtime.py` — real stdlib-only remote program |
| EDIT | `src/fleet_rlm/daytona/workspace_agent.py` — validation, serialization, `importlib.resources` load, decode, host exception mapping; remove giant `"\n".join((...))` (~73–1252) |
| EDIT | `scripts/validate_release.py` — require runtime source in wheel |
| EDIT | Workspace Agent unit tests — golden protocol lock before extract |

**Implementation notes:**

- Lock golden behavior first (traversal, symlink, FIFO, oversized read, checksum, atomic write, append, patch unique/missing/ambiguous, empty-dir delete, non-empty conflict, Memory mutation, fsync warning, WORM fallback), then extract.
- Load via `importlib.resources`, not host import of remote entrypoints.

**Validation gate:**

```bash
uv run pytest tests/unit/backend/daytona/test_workspace_agent_stat.py \
  tests/unit/backend/daytona/test_workspace_agent_delete_patch.py -q
make build-release
make check-release
```

**Acceptance:**

- [x] Remote agent is readable Python source in the package/wheel
- [x] Golden protocol tests green before and after
- [x] Wheel required-file assertion includes the runtime module

---

### Mission 11 — Reload `UIMessagePart` discriminated union

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 10
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 11

**Purpose:** Replace the reload mega-envelope with exact per-variant models + Pydantic discriminator while keeping serialized JSON identical.

**Non-goals:**

- Removing snake/camel live-transport compatibility
- API redesign / field renames
- Hand-editing `openapi.yaml` or generated TUI types

**Compatibility:** Serialized reload JSON must not change; OpenAPI regenerates via `make api-sync`.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `src/fleet_rlm/api/schemas.py` — replace mega-envelope `UIMessagePart` (~133–163) with discriminated variants |
| REGEN | `openapi.yaml` |
| REGEN | `tools/fleet-tui/src/generated/openapi.ts` |
| EDIT/ADD | contract tests / fixtures for every durable part variant |

**Implementation notes:**

- Pattern: `TextUIMessagePart`, `ReasoningUIMessagePart`, `DynamicToolUIMessagePart`, … + `Annotated[..., Field(discriminator="type")]`.
- For each variant: old fixture JSON → validates → serialization equals fixture.

**Validation gate:**

```bash
make api-sync
make api-check
uv run pytest tests/contracts/backend -q
```

**Acceptance:**

- [x] Discriminated union in schemas
- [x] Wire JSON unchanged vs fixtures
- [x] `make api-check` green; TUI generated types updated only via sync

---

### Mission 12 — Split TUI live vs durable projection

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 11
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 12

**Purpose:** Separate live SSE projection from durable reload projection so each contract is obvious.

**Non-goals:**

- `AbstractProjectionEngine` / strategy / registry
- Changing `fleet-turn-stream.ts` stream-order grammar
- Changing `store.ts` reducer model
- Removing snake/camel dual-field reads

**Compatibility:** Final user-visible live and durable projections remain equivalent where intended; dual camel/snake tolerance retained.

**Files:**

| Action | Path |
|--------|------|
| ADD | `tools/fleet-tui/src/tui/live-projection.ts` — from `LiveTurnProjector` (~9–281) |
| ADD | `tools/fleet-tui/src/tui/durable-projection.ts` — from `projectDurableTurns` (~292+) |
| ADD | `tools/fleet-tui/src/tui/projection-helpers.ts` — only tiny shared pure helpers |
| EDIT | `tools/fleet-tui/src/tui/projection.ts` — re-export shim or delete after call-site update |
| EDIT | `tools/fleet-tui/src/tui/tests/projection.test.ts` (+ `runner.test.ts` imports) |
| EDIT | `tools/fleet-tui/AGENTS.md` — fix ownership (projection ≠ `transcript.ts`) |

**Validation gate:**

```bash
cd tools/fleet-tui && pnpm test  # or the package's projection/test script
cd tools/fleet-tui && pnpm exec biome check .
```

**Acceptance:**

- [x] Live and durable projection live in separate modules
- [x] Stream grammar and store reducer untouched in semantics
- [x] Dual-field reads preserved
- [x] AGENTS.md ownership corrected

---

### Mission 13 — Small consolidation / deletion sweep

- **Tier:** `recommended`
- **Status:** `done`
- **Depends on:** Mission 12
- **Narrative:** `IMPLEMENTATION_PHASES.md` §PR 13

**Purpose:** Delete proven low-risk duplication after the larger extractions land.

**Non-goals:**

- Broad comment/docstring cleanup campaign outside touched files
- Changing Skill `allowed-tools` authorization semantics
- Large unrelated refactors

**Compatibility:** Behavior-preserving edits only.

**Files:**

| Action | Path |
|--------|------|
| EDIT | `src/fleet_rlm/composition/daytona.py` — `dataclasses.replace(...)` instead of field-by-field `RuntimeInventory(...)` rebuild (~425–445) |
| EDIT | `src/fleet_rlm/composition/testing.py` — same pattern (~342–361) |
| EDIT | Memory injection sites — reuse already-normalized query |
| EDIT | Skills manifest layer — stale diagnostics/comments only |
| EDIT | Already-touched files — delete restating Parameters/Returns docstrings; keep “why” comments |

**Validation gate:**

```bash
uv run pytest tests/unit/backend/composition tests/unit/backend -q -k 'composition or skill or memory'
# then smallest lane covering touched files
```

**Acceptance:**

- [x] Inventory rebuilds use `dataclasses.replace`
- [x] No Skill authorization semantic change
- [x] Only restating comments removed; architecture “why” comments kept

---

## RC certification — mission 14

### Mission 14 — Release-candidate certification freeze

- **Tier:** `rc`
- **Status:** `in_progress`
- **Depends on:** Missions 01–13 (recommended replacement cut); **minimum** cut may proceed after 01–06 only if product accepts higher clarity debt
- **Narrative:** `IMPLEMENTATION_PHASES.md` §6

**Purpose:** Stop architecture changes and prove the branch with one RC commit across deterministic, Python-compat, persistence, live Daytona, public transport, and wheel gates.

**Non-goals:**

- Further module extractions during RC
- Comparing against `main` databases
- Requiring identical internal live vs durable transport events (semantic equivalence only)

**Compatibility:** Freeze module ownership listed in phase §9; only bugfixes allowed after freeze.

**Validation gate (all required):**

```bash
make check
make check-security
make build-release
make check-release
make api-check
make stream-check
git diff --check
```

Plus:

- Python compatibility lanes green for **3.11 / 3.12 / 3.13**
- Persistence: clean DB `alembic upgrade head` + Session→Turn→Result; representative **dev-0.7** DB upgrade + reload + new Turn
- Live Daytona checklist: ordinary Root Turn; Workspace R/W/edit/delete; attachment read; artifact commit; explicit Memory CRUD; post-commit Memory promotion; single recursive child; **M04 two-child canary**; cancel during execution; deadline/timeout cleanup — then no leaked permits/Sandboxes/interpreters/owned background tasks
- Public transport: same semantic Turn via live SSE, replay SSE, durable reload, TUI live projection, TUI durable projection
- Wheel: required payloads present (incl. Workspace Agent runtime source after M10); TestPyPI installed-wheel smoke as in release workflow

**Acceptance:**

- [x] Architecture frozen (no further extractions; RC bugfixes only)
- [x] All deterministic gates green
- [x] Compat 3.11/3.12/3.13 green (CI-matching unit+contract lane)
- [x] Live Daytona receipt green (`.scratch/rc/live-daytona-receipt.json`, `passed: true`; durability + MVP lanes)
- [x] Public transport deterministic gates (api-check, stream-check, TUI projection tests)
- [x] Wheel/hygiene gates via `make build-release` + `make check-release`
- [x] RC commits identified: freeze `834145f71` + post-freeze heartbeat fix `6cb836c0e`

**RC commits:**

- Freeze: `834145f71` — RC certification hygiene and compat bugfixes
- Follow-up RC bugfix: `6cb836c0e` — keep dead local MLflow from starving Turn claim heartbeats

**RC bugfixes included in the freeze commit:**

- `RunEventStream` Protocol no longer subclasses `AsyncIterator` (Python 3.11)
- `recursive_batch` submit-time `copy_context` + typed worker wrapper
- `preparation_attempt` `Coroutine` typing; trajectory `isinstance` narrowing; memory `Literal` version
- Lint/format/`ty` leftovers; unused test `**kwargs` renames
- TUI tests pin `COLORTERM=truecolor` for deterministic truecolor assertions

**Still open inside Mission 14 (beyond bounded verifier receipt):**

- Persistence: representative **dev-0.7** DB upgrade + reload + new Turn (if not already evidenced)
- Broader live Daytona checklist items not fully covered by the bounded MVP/durability receipt (explicit Memory CRUD live, post-commit Memory promotion live, cancel/deadline cleanup proof)
- External promotion / human approval on the receipt (`ci` / `human_approval` still `pending`)

**Optional live extras after receipt green (this session):**

- [x] M04 two-child recursive batch canary re-run (`tests/live/backend/test_daytona_recursive_batch.py`, ~74s)
- [x] Memory candidate promotion live (`tests/live/backend/test_memory_candidate_live.py`, ~174s)

---

## Cutover — mission 15

### Mission 15 — Repository cutover protection

- **Tier:** `cutover`
- **Status:** `open`
- **Depends on:** Mission 14 **and** default-branch / rebase switch complete
- **Narrative:** `IMPLEMENTATION_PHASES.md` §7

**Purpose:** After the replacement branch becomes the protected default, require the canonical CI status checks on merges.

**Non-goals:**

- Configuring protection on a temporary branch that will disappear before cutover
- Changing CI job graph beyond what M05 already landed

**Required status checks (minimum):**

- quality
- lint-typecheck
- test-unit
- test-e2e
- daytona-coverage
- tui
- Python compatibility

**Acceptance:**

- [ ] Branch protection enabled on the post-cutover default branch
- [ ] Required checks match the list above
- [ ] Merges without green required checks are blocked

---

## Explicitly deferred (after replacement)

Do **not** schedule these inside missions 01–15:

- [ ] Native async DSPy interpreter / `RLM.acall()` migration off the sync bridge
- [ ] Root/Sub model tuning campaigns
- [ ] Public snake/camel casing removals on live transport
- [ ] AssistantPart ↔ durable dataclass unification spike
- [ ] Generic Workspace/Project Tool host merge
- [ ] Additional `dspy_contract.py` splitting (except maybe later `rlm/dspy_tracing.py`)
- [ ] `AsyncDaytonaVolumeFS` / sync volume adapter rewrite
- [ ] Blanket comment/docstring cleanup campaign

---

## Out of scope / do-not-do (this program)

From `IMPLEMENTATION_PHASES.md` §5 — agents must not invent these as missions:

1. **Do not** generically merge `workspace_tools.py` and `project_tools.py` into a declarative Tool framework unless a clear net deletion is proven (not a blocker).
2. **Do not** split `dspy_contract.py` into many tiny modules during this program.
3. **Do not** collapse durable dataclasses and Pydantic AssistantPart models yet.
4. **Do not** rewrite Volume FS adapters for line-count reasons.
5. **Do not** start the async DSPy RLM migration before cutover.

Also preserve architectural invariants from phase §3 (native child depth 1, width not depth, fresh child Sandboxes, Root final-answer authority, RuntimeEvent ≠ durable AssistantPart ≠ live chunk, Alembic-head schema validation, accepted-stream SSE, mounted Workspace Agent as privileged mutation boundary, immediate vs post-commit Memory rules).

---

## Quick claim cheat-sheet

| Next claim | When |
|------------|------|
| Mission 01 | Always first |
| Missions 02–06 | Sequentially after prior `done` |
| Missions 07–13 | After M06 for recommended replacement |
| Mission 14 | After chosen cutoff (min M06 or full M13) |
| Mission 15 | Only after default-branch switch |
