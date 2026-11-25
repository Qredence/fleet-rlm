# Fleet-RLM

**Fleet-RLM** is a stateless, recursive AI agent workspace powered by **DSPy 3.5 (`dspy.RLM`)**, isolated **Daytona** Python sandboxes, **Neon Postgres 18**, and a **React** SPA. Rather than stuffing large files into an LLM prompt window, Fleet-RLM stores Input Files in a persistent per-session Workspace, writes and runs Python to analyze and cross-check data, delegates bounded recursive subtasks, streams live execution traces to the browser, and curates long-term Memory across sessions.

---

## Capabilities

- **Programmatic Long Context:** Upload CSV/Parquet datasets, multi-sheet Excel workbooks, PDFs, or code (up to 100 MiB) into `/workspace/files`. The agent inspects and transforms data using `pandas`, `pyarrow`, `openpyxl`, `pypdf`, `scipy`, `matplotlib`, and `pillow`, then saves downloadable Artifacts to `/workspace/artifacts`.
- **Recursive Reasoning & Live Traces:** Complex Tasks decompose dynamically into concurrent `llm_query_batched` map-reduce calls and isolated recursive `spawn_agent` Subagent Runs under configurable Recursion Depth and Call Budget limits, streamed natively via AI SDK v1 SSE into nested UI traces.
- **Progressive-Disclosure Skills (`@skill`).** The Daytona image includes seven read-only `SKILL.md` Skills (`data-analysis`, `spreadsheet-audit`, `document-extraction`, `chart-visualization`, `scientific-computing`, `recursive-synthesis`, and `skill-creator`). TypeSafe Jev (`Noul` + `Choice`) scores them per turn. You can also pin them with `@skill`.
  Open a generated `SKILL.md` in the Artifacts preview and choose **Install skill** to make it available in the Skills picker across sessions. Installs are stored in the Postgres `installed_skills` table, so every backend instance sees them. Installed skills are data only and never load host tools.
- **4-Tier Memory with Human Review:** Pairs ephemeral per-Run Execution Contexts and bounded Episode History with Neon full-text searchable Session/Global Memories (`tsvector` GIN) consolidated after each Turn (high-confidence facts auto-approved; lower-confidence candidates queued in the UI for review) and versioned offline Compiled Programs / Calibrated Gates.
- **Stateless Workers, Durable State:** Any FastAPI worker can handle a Turn. Postgres stores the session, run tree, and file manifest. Private Neon Object Storage retains uploads and published artifacts across Daytona VM replacement. Scratch files and checkouts stay on the current VM.
- **Offline Optimization & Observability:** Out-of-band `dspy.GEPA` program compilation and 5-fold `ReAnchor` gate calibration against isolated Neon branches, paired with external MLflow 3.18 tracing.

---

## Quick Start

**Prerequisites** include Python `3.13` with `uv`, Node.js `26.13.1` with pnpm `12.23.0`, **Neon Postgres 18** with pooled and direct URLs, a private **Neon Object Storage** bucket on the same branch, a **Daytona v2** API key and image, and LLM and TypeSafe credentials. Local MLflow also uses Docker.

For frontend development, select the Node version in [`.node-version`](./.node-version) before running the install or `make` commands. These frontend tool versions match CircleCI.

1. **Configure environment variables:**
   ```bash
   cp backend/.env.example backend/.env
   ```
   Set the Postgres URLs, `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_IMAGE`, `FLEET_DAYTONA_REGION`, and the `FLEET_S3_*` variables shown in the example. Set `FLEET_LLM_MODEL`, `FLEET_SUB_LLM_MODEL`, `FLEET_LLM_API_KEY`, and `TYPESAFE_API_KEY`. Unset `DAYTONA_API_URL` and `DAYTONA_V2_API_URL` when running the v2 client. See [Build and roll out the sandbox image](#build-and-roll-out-the-sandbox-image).

   > Models, RLM and history budgets, and the Python cell timeout can be edited in **Settings**. Environment variables control the Daytona image, region, and lifecycle, the storage bucket, the model gateway, MLflow, and worker concurrency.

2. **Install dependencies and run database migrations:**
   ```bash
   cd backend && uv sync --python 3.13 && uv run alembic upgrade head
   cd ../frontend && pnpm install --frozen-lockfile
   ```
   For an existing v0 deployment, rehearse the Alembic upgrade through `0006_workspace_recovery` on an isolated branch. Revision `0004_daytona_v2_cutover` clears old sandbox bindings and file manifests. Conversations and memories are retained. Users re-upload any files they want in the new workspace. Keep the old Volume and the pre-cutover database restore point for the migration window. [Migration plan](./docs/daytona-v2-migration-plan.md)

3. **Start external MLflow *(optional — port `5001` avoids macOS AirPlay on `5000`)*:**
   ```bash
   make mlflow
   ```
   With `FLEET_MLFLOW_TRACKING_URI` configured, admitted chat requests record a `fleet.turn` span in an MLflow trace keyed by `fleet.run_id`. The span covers sandbox acquisition, workspace hydration, RLM execution, artifact publication, and finalization. To include Daytona SDK request spans, set both `FLEET_DAYTONA_OTEL_ENABLED=true` and `MLFLOW_USE_DEFAULT_TRACER_PROVIDER=false` in the backend process environment.

4. **Run the backend and frontend:**
   ```bash
   make dev-backend   # FastAPI on http://127.0.0.1:8000
   make dev-frontend  # Vite SPA on http://localhost:5173 (proxies /api, /healthz, /readyz)
   ```

### Build and roll out the sandbox image

Use the Daytona CLI for the target project and set `FLEET_DAYTONA_REGION` to the deployment region. Archive a committed revision. The archive contains the Dockerfile, locked dependencies, and tracked bundled Skills.

```bash
revision=$(git rev-parse HEAD)
context=$(mktemp -d)/fleet-daytona-context.tar
make daytona-image-context REV="$revision" OUT="$context"
printf '%s\n' '{"cpu_arch":"amd64"}' | daytona image build "fleet-rlm-$(git rev-parse --short=12 "$revision")" \
  --region "$FLEET_DAYTONA_REGION" --dockerfile backend/snapshot/Dockerfile \
  --context "$context" --file - --yes
```

For dependency updates, edit the input requirements and refresh the Linux amd64 Python 3.13 lock with `uv`.

```bash
uv pip compile backend/snapshot/requirements.in --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu --generate-hashes \
  --only-binary :all: -o backend/snapshot/requirements.lock
```

Record the source revision, Daytona image ID, region, and architecture. Use the ready `img_…` ID as `FLEET_DAYTONA_IMAGE` in staging, then verify a fresh session. Before increasing traffic, check `daytona org limits` and the live sandbox inventory against expected concurrent cold sessions across the deployment's backend workers. Reduce `FLEET_MAX_CONCURRENT_TURNS_PER_WORKER` if headroom is insufficient. Promote the tested backend and image ID together. Existing workspaces age out under the configured lifecycle. Keep the prior backend revision and image ID for rollback.

---

## Architecture & Stack

- **Backend modules** include `interpreter.py` for Daytona, `storage.py` for host-side S3, `db.py` for Postgres, and `agent.py` for DSPy. The `import-linter` contracts enforce these boundaries.
- **`frontend/`:** Zero-BFF 3-pane monochrome UI (`SessionsSidebar`, main chat workspace with `ChatComposer`, `PythonToolTrace`, and recursive `SubagentTrace`, plus `WorkspaceSidePanel` and `SettingsDialog`) consuming `x-vercel-ai-ui-message-stream: v1` via `@ai-sdk/react` `useChat`.

| Component | Version / Target | Role |
| :--- | :--- | :--- |
| **Python** | `>=3.13,<3.14` | Host API, offline optimizer, and Daytona image parity |
| **FastAPI** | `fastapi[standard]>=0.165.0` | Stateless REST + native `EventSourceResponse` SSE over `POST` |
| **DSPy** | `dspy==3.5.0` | `dspy.RLM`, `dspy.Flex`, `dspy.History`, `dspy.GEPA`, `ReAnchor`, `Noul`/`Choice` |
| **Daytona** | `daytona[otel]==2.1.0` (`.v2`) | Linux microVMs and one streamed Python worker per RLM invocation |
| **Database** | Neon Postgres 18 (`sqlalchemy>=2.1.9`, `alembic>=1.20.1`) | `uuidv7()`, recursive `runs`, single-writer turn lock, `memories`, `workspace_settings` |
| **Object storage** | Neon Object Storage using `boto3` | Private uploads and published artifacts verified by SHA-256 |
| **Observability** | MLflow `>=3.18.0` (external) | `mlflow.dspy.autolog()` + offline compile/eval artifact tracking |
| **Frontend** | Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui | Native AI SDK v1 streaming chat, file tree, memory review, settings |

---

## Offline Optimization (`optimize.py`)

Run out-of-band optimization against an ephemeral **Neon branch** (`optimize.py` refuses configured primary URLs unless `--allow-direct` is passed):

```bash
cd backend
uv run python -m fleet_rlm.optimize --target rlm --database-url "$NEON_BRANCH_URL"
uv run python -m fleet_rlm.optimize --target skills --database-url "$NEON_BRANCH_URL" --dataset skills.jsonl
uv run python -m fleet_rlm.optimize --target memory --database-url "$NEON_BRANCH_URL" --dataset memory.jsonl
```

When deploying `ReAnchor`-calibrated `skill_selector.json` or `memory_gate.json` gates, pin the exact TypeSafe Jev snapshot in `FLEET_DECISION_MODEL` instead of floating `typesafe:jev-latest`.

---

## Verification & Documentation

```bash
make check
```

Runs backend checks (`ruff check`, `ruff format --check`, `lint-imports`, `ty check src`, `pytest`) and frontend checks (`pnpm lint`, `pnpm typecheck`, Vitest `pnpm test`, `pnpm build`).

- [GLOSSARY.md](./GLOSSARY.md) — Canonical domain vocabulary (`Session`, `Workspace`, `Artifact`, `Turn`, `Run`, `Deployment Settings`, `Compiled Program`, `Calibrated Gate`)
- [docs/PRD.md](./docs/PRD.md) — Architecture specification and non-negotiable engineering guardrails
- [AGENTS.md](./AGENTS.md) — Developer guide, module boundaries, and verification commands
- [frontend/README.md](./frontend/README.md) — Frontend development notes
- [CHANGELOG.md](./CHANGELOG.md) — Release history and migration notes
