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

## PR-B — fix(turns): no-progress guard terminal event + stream dedupe (critical) — `codex/turn-guard-terminal-event`  ✅ done in `40d4a34d5`
- [x] Verify TUI strict-lifecycle requirement for terminal `is_final` per output stream (`tools/fleet-tui/src/sse.ts`, `tui/projection.ts`) — decides flush shape
- [x] `rlm/tool_observer.py`: emit `ToolFailed` before `TurnNoProgressError` raise
- [x] `rlm/runner.py` `_reconcile_trajectory`: payload-identity comparison; identical content never re-emitted; corrections still upsert same stream_id
- [x] `daytona/interpreter.py`: final output flush = unsent tail only (use `emitted_chars`); no output after SUBMIT
- [x] Tests: observer sequence, commit policy with no-progress history, trajectory dedupe, interpreter flush
- [x] Live replay: zero duplicate `data-rlm-output` frames per capture; guard-storm turn commits

## PR-C — fix(api): workspace stat checksum + root stat — `codex/workspace-stat-checksum`  ✅ done in `b856bf4e3`
- [ ] FOLLOW-UP (from Phase C review): `_DaytonaWorkspaceFileSession._read_current` (write/append expected_sha256 preconditions) still reads full text → same >10_000 400 class on big files; switch to checksum-enabled stat like the PR-C fix.
- [x] `daytona/workspace_agent.py`: optional sandbox-side `checksum` on stat op
- [x] `daytona/workspace_fs.py`: flag pass-through (sync+async)
- [x] `daytona/workspace_gateway.py`: stat via flag; drop full-content read
- [x] `files/workspace_access.py`: `allow_root=True` for stat
- [x] Tests: gateway stat file/dir/missing; `/api/files/stat` contract tests
- [x] Live replay: root+nested stat 200 with sha256

## PR-D — feat(api): preparation heartbeats + cancel tombstone — `codex/pre-run-heartbeat`  ✅ done in this commit (live: first frame 32 ms, tombstone persisted)
- [x] `api/routes/turns.py`: generator-spanning open(); transient `data-status{phase:"preparation"}` at ~1 s then every `heartbeat_seconds`; prelude never recorded
- [x] `chat/turn_lifecycle.py` + `turn_detail_policy.py`: cancelled-turn tombstone part persisted (D2)
- [x] Docs: `abort` frame semantics (D3)
- [x] Tests: heartbeat cadence/transience, stream contract first-frame budget, tombstone persistence+listing
- [x] Live replay: cancel lane shows ≤1 s first frame + tombstone in GET /turns

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


## RC-8 — fix(chat): committed terminal must win over post-commit claim revoke  ✅ done in `040d957dc`
- [x] Symptom: successful recursive turn committed (checkpoint written, turns persisted), then detached cleanup raced a stale-claim revoke into `turn_claim._revoke` → InvalidClaimTransitionError("a committed Run cannot be revoked") → live stream emitted `Turn failed` AFTER the commit. Durable truth and stream terminal disagreed.
- [x] Fix: revoke/cleanup paths treat already-committed claim as benign no-op (log + complete settling, never emit failure); run one recursion replay lane proving finish=stop and persisted roles+answer.

## RC-7 + deadlock root cause (DIAGNOSED 2026-08-08 via faulthandler SIGUSR1 capture, /tmp/fleet-dogfood/threaddump.txt)
- [x] Root cause pinned: nested sync wait deadlock — tool execution (e.g. load_skill _install_resources) sync-calls run_workspace_agent through _SyncCodeInterpreter.code_run/_sync_await (interpreter.py:303-327) which posts the sandbox coroutine to the RLM WORKER THREAD'S OWN asyncio loop (runner.py:957 asyncio.run), while that worker thread is itself parked in nested Future.result() inside http_broker._poll_once. Circular wait: worker waits broker-answer; answer executor waits the worker's loop. Any tool whose host impl does sync FS work on the worker-owned bridge deadlocks the entire turn; uvicorn starves draining the turn generator (RC-7 wedge).
- [ ] FIX (WS-RC7): give _Sync* sandbox bridges (interpreter.py:306-380) a DEDICATED daemon loop thread instead of capturing the caller's running loop; alternatively remove nested sync waiting in the broker fulfill path. Regression test must reproduce the deadlock shape (sync bridge inside a nested-wait coroutine).


## RC-11 — fix(api): /api/volume/tree lists nothing anywhere (FOUND in Phase E live verification)
- [ ] Symptom: GET /api/volume/tree?root=<anything> returns paths:[] (+root hint dirs only) even though the same volume provably contains artifacts/, attachments/, files/dogfood/rest-probe.txt, projects/fleet-rlm/decisions/tool-forwarding.md (all visible via /api/files and via live RLM project tools). gateway.list_files (WorkspaceVolumeGatewayDep → daytona/workspace_fs.list_files → sandbox.fs.list_files + filtering) yields zero entries at every root (all filters fresh-cache tested). /api/files works — so REST gateway+mount are fine; bug sits in the tree-specific gateway/list_files path (entry filtering or its sandbox binding).
- [ ] Also ergonomic inconsistency: tree uses query param `root` while /api/files uses `path` (documentation + maybe alias).
- [ ] Fix with a contract test: seed two nested volumes paths; assert tree returns them.

## Follow-up investigation — RC-7 server wedge under load+disconnect (UNSCHEDULED, file after phase A)
- [ ] Symptom: after 4 concurrent live turns incl. 1 recursive child (~16 min of throttled LLM rounds) and 3 client disconnects within seconds, the ASGI server froze completely: frozen log, `/openapi.json` (static) unresponsive >15 min. macOS `sample`: main thread spinning in uvloop idle → async_gen_asend chains = runaway coroutine starving the loop.
- [ ] Controlled repro so far: 3 mid-run client disconnects alone do NOT wedge (server stayed healthy; orphaned turn completed server-side). Suspects: recursive child runtime settlement, settlement-on-disconnect path with in-flight recursive child, or a no-await loop in cleanup/drain under lease pressure. Needs faulthandler+SIGUSR1 reproduction with the recursion lane included (harness: /tmp/fleet-dogfood/fault_server.py, threaddump.txt path).
- [ ] Fix owner: after PR-A merges; add to TODOS when scheduled.
