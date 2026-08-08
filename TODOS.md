# Fleet RLM — TODOS

Program: tool-surface revival + volume architecture (canonical plan: `plans/tool-surface-revival.md`)
Base: `dev-0.7` (no `main`/`master` targets). Evidence: `/tmp/fleet-dogfood/` dogfooding 2026-08-08.
Each PR: branch → focused suite → full gate (make check incl. TUI via pnpm 10.33.2, check-security, build/check-release, check-docs, git diff --check) → live dogfood spot-run → PR → babysit → merge (merge-commit, keep branch).

## PR-A — fix(rlm): kwargs-only tool forwarding + error surfacing (critical) — `codex/broker-kwargs-only-tools`  ✅ done in `1a2f3ea1e` (+ latent drain fix)
- [x] `daytona/http_broker.py` `_tool_wrapper_source`: forward all params as kwargs (args only for POSITIONAL_ONLY); assert no positional-only params on current surface
- [x] `daytona/http_broker.py`: module logger + WARNING per host tool failure (name + sanitized message only)
- [x] `daytona/broker_source.py` `TOOL_WRAPPER_TEMPLATE`: read HTTPError body, raise `RuntimeError("Tool call failed: <safe message>")`
- [x] `daytona/interpreter.py`: kwargs-only contract comment at `tool_executor`
- [x] Tests: broker wrapper wire-payload (stubbed urlopen), kwargs-only executor regression via real pinned DSPy `_make_interpreter_tool`, `rlm_query` payload
- [x] Live replay: lanes fetch/write/recursion/report green; report turn commits artifact

## PR-B — fix(turns): no-progress guard terminal event + stream dedupe (critical) — `codex/turn-guard-terminal-event`
- [ ] Verify TUI strict-lifecycle requirement for terminal `is_final` per output stream (`tools/fleet-tui/src/sse.ts`, `tui/projection.ts`) — decides flush shape
- [ ] `rlm/tool_observer.py`: emit `ToolFailed` before `TurnNoProgressError` raise
- [ ] `rlm/runner.py` `_reconcile_trajectory`: payload-identity comparison; identical content never re-emitted; corrections still upsert same stream_id
- [ ] `daytona/interpreter.py`: final output flush = unsent tail only (use `emitted_chars`); no output after SUBMIT
- [ ] Tests: observer sequence, commit policy with no-progress history, trajectory dedupe, interpreter flush
- [ ] Live replay: zero duplicate `data-rlm-output` frames per capture; guard-storm turn commits

## PR-C — fix(api): workspace stat checksum + root stat — `codex/workspace-stat-checksum`
- [ ] `daytona/workspace_agent.py`: optional sandbox-side `checksum` on stat op
- [ ] `daytona/workspace_fs.py`: flag pass-through (sync+async)
- [ ] `daytona/workspace_gateway.py`: stat via flag; drop full-content read
- [ ] `files/workspace_access.py`: `allow_root=True` for stat
- [ ] Tests: gateway stat file/dir/missing; `/api/files/stat` contract tests
- [ ] Live replay: root+nested stat 200 with sha256

## PR-D — feat(api): preparation heartbeats + cancel tombstone — `codex/pre-run-heartbeat`
- [ ] `api/routes/turns.py`: generator-spanning open(); transient `data-status{phase:"preparation"}` at ~1 s then every `heartbeat_seconds`; prelude never recorded
- [ ] `chat/turn_lifecycle.py` + `turn_detail_policy.py`: cancelled-turn tombstone part persisted (D2)
- [ ] Docs: `abort` frame semantics (D3)
- [ ] Tests: heartbeat cadence/transience, stream contract first-frame budget, tombstone persistence+listing
- [ ] Live replay: cancel lane shows ≤1 s first frame + tombstone in GET /turns

## PR-E — feat(workspace): `projects/<slug>/` browsable deliverables root — `codex/project-workspace-root`
- [ ] `files/volume_paths.py`: `projects_root()`/`project_dir(slug)` + slug/reserved/traversal validation
- [ ] `files/workspace_tools.py`: `write/read/stat/list_project_*` tools (overwrite+expected_sha256 semantics)
- [ ] `rlm/tool_guards.py`: project path obligations
- [ ] `skills/bundled/report-builder`, `workspace-files`: convention docs (scratch→sessions, deliverables→projects)
- [ ] Tests: slug matrix, reserved rejection, guards, promotion from project path
- [ ] Live replay: report lane writes `projects/<slug>/...` + volume tree browse

## PR-F — feat(memory): lifecycle + per-turn injection (tenant deferred) — `codex/memory-lifecycle`
- [ ] `files/memory_models.py`: v2 ids `<!-- id:8hex -->`, v1-compatible; tolerant reads (skip malformed lines + warning)
- [ ] Port + `daytona/workspace_memory.py`: `list_entries`, `delete_entry`, `edit_entry` (one atomic round trip)
- [ ] `files/memory_tools.py`: `remember` (alias kept), `list_memories`, `edit_memory`, `forget`
- [ ] Move store to `memory/MEMORIES.md` with legacy migration on open
- [ ] `daytona/run_environment.py`: ≤4 KiB tail digest injected per turn (mtime-cached 30 s)
- [ ] Tests: v1/v2 parse, tolerant read, CRUD round trips, injection budget
- [ ] Live replay: turn 2 remembers turn 1 preference without tool calls; forget edits exactly one entry

## PR-G — feat(volume): full CRUD — `codex/volume-full-crud`
- [ ] `daytona/workspace_agent.py`: `delete` + `patch` ops (atomic, symlink/traversal/non-empty-dir safe)
- [ ] `daytona/workspace_fs.py`: `delete_path`, `patch_text` (sync+async) + cache invalidation
- [ ] `files/workspace_tools.py`: `delete_workspace_path`, `edit_workspace_text` (allowlisted roots only; catalog roots rejected)
- [ ] `api/routes/workspace_files.py`: `DELETE`/`PATCH /api/files/content` (404/409 mapping)
- [ ] `make api-sync` (openapi.yaml + TUI generated types)
- [ ] `rlm/tool_guards.py`: delete/edit obligations
- [ ] Tests: fs delete/patch, API contract, guards
- [ ] Live replay: write→edit→read→delete→list on session+project roots

## PR-H — chore(backend): `src/fleet_rlm` readability (mechanical, last) — `codex/backend-readability`
- [ ] `daytona/workspace_fs.py`: single async FS core + thin sync bridge (~−300 LOC)
- [ ] `daytona/interpreter.py`: extract output projection → `daytona/interpreter_output.py` (~−150 LOC)
- [ ] `src/fleet_rlm/CONTEXT.md`: workspace-module naming map (fs/gateway/access/tools) + daytona/ module one-liners
- [ ] Gate: `check-codebase-tree` + full make check green; zero behavior change

## Follow-up investigation — RC-7 server wedge under load+disconnect (UNSCHEDULED, file after phase A)
- [ ] Symptom: after 4 concurrent live turns incl. 1 recursive child (~16 min of throttled LLM rounds) and 3 client disconnects within seconds, the ASGI server froze completely: frozen log, `/openapi.json` (static) unresponsive >15 min. macOS `sample`: main thread spinning in uvloop idle → async_gen_asend chains = runaway coroutine starving the loop.
- [ ] Controlled repro so far: 3 mid-run client disconnects alone do NOT wedge (server stayed healthy; orphaned turn completed server-side). Suspects: recursive child runtime settlement, settlement-on-disconnect path with in-flight recursive child, or a no-await loop in cleanup/drain under lease pressure. Needs faulthandler+SIGUSR1 reproduction with the recursion lane included (harness: /tmp/fleet-dogfood/fault_server.py, threaddump.txt path).
- [ ] Fix owner: after PR-A merges; add to TODOS when scheduled.
