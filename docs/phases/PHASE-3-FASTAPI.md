# Phase 3 — Stateless FastAPI 0.165.0 API Server

## Objective
Wire `config.py`, `db.py`, `interpreter.py`, `skills.py`, and `agent.py` into a thin, horizontally scalable FastAPI 0.165.0 application (`api.py`) with zero in-process session state, native POST SSE streaming (`EventSourceResponse`), upload size guards, per-worker concurrency limiting, and disconnect cancellation.

## Files to Create
- `backend/src/fleet_rlm/api.py`
- `backend/tests/test_phase3_api.py`

## Technical Specifications

### 1. Application Setup & `lifespan`
- Create `FastAPI(title="Fleet RLM", lifespan=lifespan, max_body_size=1 * 1024 * 1024)` (1 MiB default JSON cap).
- Add `CORSMiddleware` using `settings.cors_origins`, exposing `x-vercel-ai-ui-message-stream`.
- Inside `lifespan(app)`:
  1. Load `Settings()`.
  2. Initialize Neon engine + sessionmaker via `create_db(settings)` and run `reap_stale_runs(db, settings.stale_run_timeout_seconds)`.
  3. If `settings.mlflow_tracking_uri` is set: `mlflow.set_tracking_uri(...)`, `mlflow.set_experiment(...)`, `mlflow.dspy.autolog()`.
  4. Load immutable `SkillRegistry.load(settings.skills_dir)`.
  5. Open single shared `AsyncDaytona(DaytonaConfig(...))` context manager.
  6. Instantiate default `dspy.LM`, `sub_lm`, `decision_lm`, and base `dspy.RLM` program.
  7. Store read-only singletons on `app.state`. Dispose SQLAlchemy engine on shutdown.

### 2. Dependency Injection Rules (FastAPI 0.165.0)
- `require_auth`: constant-time `secrets.compare_digest` Bearer check (passes if `api_bearer_token is None` for local dev).
- `DbDep = Annotated[AsyncSession, Depends(get_db, scope="function")]`: used **only** on non-streaming REST endpoints.
- `TurnConcurrencySlot = Annotated[None, Depends(turn_slot, max_concurrency=16)]`: single shared `Depends` alias capping active streams per Uvicorn worker without manual semaphores.
- **SSE DB rule:** Inside `/api/sessions/{session_id}/chat`, never inject `DbDep`. Use short explicit `async with sessionmaker() as db:` blocks before streaming, on child-run persistence, and in `finally` cleanup.

### 3. Exact 7-Endpoint API Surface

1. **Health & Readiness (Replaces legacy setup HTTP server)**
   - `GET /healthz` -> `{"status": "ok"}`
   - `GET /readyz` -> checks `SELECT 1` on Neon + confirms `AsyncDaytona` client initialized; returns `200` or `503`.
2. **Sessions CRUD**
   - `POST /api/sessions` -> creates session row; returns `{id, title, created_at}`.
   - `GET /api/sessions` -> lists recent sessions ordered by `updated_at DESC`.
   - `DELETE /api/sessions/{session_id}` -> deletes session row (and stops/deletes Daytona sandbox if attached).
3. **UI Hydration (For `@ai-sdk/react` `useChat`)**
   - `GET /api/sessions/{session_id}/messages` -> returns `get_session_ui_messages(db, session_id)` directly as `UIMessage[]`.
4. **Recursive Chat Stream (`POST` SSE)**
   - `POST /api/sessions/{session_id}/chat` with `response_class=EventSourceResponse`, `dependencies=[Depends(require_auth)]`, returning `AsyncIterable[ServerSentEvent]`.
   - Accepts AI SDK `useChat` payload (`{ messages: [...], pinned_skills?: [...] }`) and extracts the latest user prompt.
   - Sets headers: `response.headers["x-vercel-ai-ui-message-stream"] = "v1"` and `response.headers["X-Accel-Buffering"] = "no"`.
   - Flow:
     1. Short DB checkout: `get_session_or_raise`, `acquire_root_run` (`409 Conflict` on `TurnConflictError`), `ensure_session_sandbox` (persist `daytona_sandbox_id` if created), `list_session_files`, `search_memories`, deserialize `dspy.History`.
     2. Run `select_skills` (Jev or `@skill` override) and emit `data-skills`.
     3. Stream `agent.stream_turn(...)` yielding `ServerSentEvent(id=f"{run.id}:{seq}", data=frame)`.
     4. Check `await request.is_disconnected()` on each chunk; on disconnect or `CancelledError`, break cleanly so `DaytonaCodeInterpreter.shutdown()` deletes the active `InterpreterContext` and marks the run `'cancelled'`.
     5. On completion: scan `/workspace/artifacts` for new files -> `upsert_file_record`, emit `data-file-tree`, emit `finish`, then yield `ServerSentEvent(raw_data="[DONE]")`.
     6. `finally`: short DB checkout calls `finish_run(...)` atomically committing `status`, `answer`, `ui_parts`, and updated `history_json`. If `status == "completed"`, run two-stage `consolidate_memory` (caught safely so memory errors never break a completed turn).
5. **File Upload (100 MiB Override)**
   - `POST /api/sessions/{session_id}/files` with `max_body_size=100 * 1024 * 1024` and `form_limits={"max_files": 1, "max_fields": 4, "max_part_size": 64 * 1024}`.
   - Sanitizes filename (no path traversal), computes SHA-256 on ingress, ensures sandbox, uploads to `/workspace/files/{name}`, upserts Neon `files` row.
6. **File Listing & Artifact Download**
   - `GET /api/sessions/{session_id}/files` -> returns `list_session_files(db, session_id)`.
   - `GET /api/sessions/{session_id}/files/{file_path:path}` -> validates path is recorded in Neon `files`, downloads bytes from Daytona `/workspace/{file_path}`, returns response with safe `Content-Disposition`.
7. **Unified Memories & Skills Inspection**
   - `GET /api/memories` (optional `session_id`, `approved`, `kind`, `scope`, and `q` filters; `before_id` pages newest-first results and is rejected with `q`; memories of deleting sessions are hidden), `PATCH /api/memories/{memory_id}` (approve/edit content), `DELETE /api/memories/{memory_id}`.
   - `GET /api/skills` -> lists read-only `SkillRegistry` entries (name + description) for the Vite `@skill` picker.

## Exit Gate (Must Pass Before Phase 4)
```bash
cd backend
uv run ruff check .
uv run lint-imports
uv run pytest tests/test_phase3_api.py
# Multi-worker statelessness smoke test:
uv run uvicorn fleet_rlm.api:app --workers 2 --port 8000
```
- **Acceptance criteria:**
  1. `test_phase3_api.py` passes auth, 409 conflict, 100 MiB upload limit vs 1 MiB JSON limit, path-traversal rejection, and AI SDK v1 SSE framing ending in `data: [DONE]`.
  2. Running `--workers 2`: creating a session on worker A, uploading a file, running a turn, and fetching `/messages` all succeed seamlessly across workers.
  3. Killing `curl -N` mid-stream marks the Neon `runs` row `'cancelled'` within 2 seconds and unlocks the session immediately.
