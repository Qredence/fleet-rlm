# Working on Fleet-RLM

## Where should I start?

Check `git status --short` and preserve unrelated work, including untracked files. Follow one request from its entry point to its callers and nearest tests. Start chat, workspace and optimization HTTP requests in `api.py`, a DSPy turn in `agent.py`, optimization subprocesses in `opt_runner.py`, and the offline CLI in `optimize.py`. For browser behavior start in the affected page or hook under `frontend/src`. The [architecture guide](docs/architecture.md) maps ownership.

Use `uv` in `backend/` and `pnpm` in `frontend/`. For dependency-specific changes check the lock, resolve the library in Context7, and query the relevant API. Consult the tagged upstream source when the index omits the installed release.

## Which contracts does the change touch?

- FastAPI belongs in `api.py`. Chat uses native EventSourceResponse and the AI SDK frames from `ui_stream.py`. Optimization streams named job events. Release DB connections before yielding.
- Postgres owns session state, root locks, and job admission. Use the pooled runtime URL with `prepare_threshold=None` and the direct migration URL with `NullPool`. Keep transactions short and allocate a separate async session for each concurrent task.
- Keep Daytona SDK calls in `interpreter.py` and S3 SDK calls in `storage.py`. Keep model, database and storage credentials on the host. Start the backend with `make dev-backend`.
- Keep Noul, Choice and Score outputs in standalone DSPy Predict gates. Reserve `history`, `repl_entry`, `variables_info` and `iteration` for DSPy. Use an isolated Neon branch for offline optimization and pin `FLEET_DECISION_MODEL` when deploying ReAnchor artifacts.

## How do I verify it?

Extend the nearest existing test. Run it first, then use the root check for the diff.

| Change | Check |
| --- | --- |
| Documentation | Check relative links and run `git diff --check` |
| Backend | `make check-backend` |
| Frontend | `make check-frontend` |
| Both or shared contracts | `make check` |

Use CircleCI for CI changes. Pull requests and main run both package jobs. Keep local checks reproducible and record commands, results and outstanding live acceptance separately.

## Where does lasting knowledge belong?

Keep runtime ownership in the architecture guide, setup in the README, terminology in the glossary, operator procedures in runbooks and dated measurements in the evaluation baseline. Retire completed plans in Git history after transferring outstanding checks.
