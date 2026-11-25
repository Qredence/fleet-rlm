# Repository Guidelines

## Make the change

- Check `git status --short` before editing. Preserve unrelated changes, including untracked files.
- Start at the entry point affected by the request. Backend code lives in `backend/src/fleet_rlm/`. Trace HTTP requests from `api.py` and agent turns from `agent.py`. For UI changes, follow the affected page, component, or hook in `frontend/src/`.
- Read the callers and nearest tests before changing a shared contract. Reuse existing code and match local conventions. Extend an existing test when it can cover the regression.
- Use `uv` in `backend/` and `pnpm` in `frontend/`. For library-specific API or configuration questions, resolve the library in Context7 and query the relevant documentation.

## Preserve runtime contracts

- **Database:** Keep session state and locks out of process. Use pooled `FLEET_DATABASE_URL` with `prepare_threshold=None` at runtime and direct `FLEET_DATABASE_DIRECT_URL` with `NullPool` for Alembic. Keep transactions short, including during streaming.
- **Chat:** Keep FastAPI imports in `api.py`. Use `EventSourceResponse` and `ui_stream.py` to emit the AI SDK stream consumed by `useChat` and `DefaultChatTransport`. Release database connections before yielding events.
- **Sandbox:** Keep Daytona SDK calls in `interpreter.py`. Keep model, database, and storage credentials on the host. Use `make dev-backend` to launch with the correct Daytona environment.
- **DSPy:** Keep `Noul`, `Choice`, and `Score` outputs in standalone `dspy.Predict` gates. Reserve `history`, `repl_entry`, `variables_info`, and `iteration` for DSPy. Run offline optimization on an isolated Neon branch and pin `FLEET_DECISION_MODEL` when deploying ReAnchor artifacts.

## Verify the change

Run the closest test while iterating. For example, use `uv run pytest tests/test_phase3_api.py` from `backend/` or `pnpm test src/lib/file-tree.test.ts` from `frontend/`.

Choose the root check that covers the final diff:

| Changed scope | Command |
| --- | --- |
| Documentation only | `git diff --check` and verify referenced paths |
| Backend only | `make check-backend` |
| Frontend only | `make check-frontend` |
| Both packages or a shared contract | `make check` |

Report the commands run, their results, and any behavior that still needs verification.
