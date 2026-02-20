# Contextual Analysis & Detailed Implementation Plan (Phases 1 & 2)

Based on the official documentation for DSPy RLM, Modal Volumes, and FastAPI Async WebSockets, here is the detailed technical analysis and implementation roadmap.

## 1. Contextual Analysis

- **FastAPI & Neon Postgres (Phase 1 Context):**
  For our Evolutive Taxonomy Memory, FastAPI handles asynchronous routing via `asyncpg`. By leveraging Neon Serverless computing alongside Postgres `pgvector` (using `vector(1536)` fields in `SQLModel`), we established a highly scalable mechanism for storing semantic observations (`AgentMemory`) tied to structural concepts (`TaxonomyNode`).
- **Modal Persisted Workspaces (Phase 2 Context):**
  Modal allows us to spin up remote Python sandbox containers instantaneously. Crucially, by mounting `modal.Volume.from_name("workspace")` at a directory like `/data/workspace`, we obtain a persistent, distributed filesystem. This allows the DSPy LLM to write a CSV file in iteration `N=1`, and then read that same CSV file in iteration `N=2` programmatically, effectively circumventing the token context window.

- **DSPy RLM API (Phase 2 & 3 Context):**
  The Recursive Language Model (RLM) fundamentally differs from prompting. We only expose raw constraints to the Agent. When the DSPy model generates code, it uses our Modal Workspace tool. If the LLM generates a dataframe that prints 50,000 rows to `stdout`, the "Context Rot" phenomenon will instantaneously corrupt its reasoning window. Therefore, protecting the context window dynamically (`max_output_chars=2000` truncation guard) is arguably the most critical component of the `execute_workspace_code` tool.

---

## 2. Detailed Roadmap (Phase 1 & Phase 2)

### 🟢 Phase 1: Infrastructure Scaffolding & Database Schema (COMPLETED)

_Status: Successfully deployed and verified via `test_db.py`._

1.  **Dependency Alignment:** Installed standard tools `fastapi[standard]`, `websockets`, `uvicorn`, `modal`, `dspy-ai`, `litellm`, `posthog`, `sqlmodel`, `asyncpg`, and `pgvector`.
2.  **Database Vector Modeling:**
    - Defined `TaxonomyNode` mapping hierarchical domain concepts.
    - Defined `AgentMemory` with contextual embeddings for DSPy `l2_distance` queries.
3.  **App Scaffolding:** Initialized FastAPI runtime explicitly requiring environment verification (`DATABASE_URL`, `MODAL_TOKEN_ID`, `POSTHOG_API_KEY`).

### 🟡 Phase 2: Execution Engine & DSPy Tools (NEXT IN QUEUE)

_Objective: Build the execution engine linking the DSPy ReAct Supervisor to the Modal physical filesystem environment and the Neon memory._

#### Step 1. Persistent Modal REPL (`src/fleet_rlm/core/modal_repl.py`)

- **Infrastructure:** Define `app = modal.App("fleet-rlm-workspace")`.
- **Filesystem Persistency:** Mount `modal.Volume.from_name("workspace", create_if_missing=True)` to `/data/workspace`.
- **Execution Runtime:** Expose an `@app.function` named `execute_chunk(code: str) -> str`. This wrapper will use Python's `exec()` mapped to capturing `sys.stdout` natively to trap printed results inside the cloud container and return them as strings to our local proxy.

#### Step 2. Establishing the Strict DSPy Guardrails (`src/fleet_rlm/core/tools.py`)

- **`@dspy.tool` execute_workspace_code:**
  - Maps to `modal_repl.execute_chunk()`.
  - **CRITICAL TRUNCATION GUARD:** Implements logic: `if len(output) > 2000: output = output[:2000] + "\n\nWARNING: Output truncated. Context window protected. Write scripts to filter or save to files instead."`
  - This forces the RLM to learn how to chunk its own data using Python (e.g., `df.head()`).
- **`@dspy.tool` search_evolutive_memory:**
  - Connects to the FastAPI `async_session` database.
  - Converts the query string into embeddings via LiteLLM (`text-embedding-3-small`).
  - Executes `order_by(AgentMemory.embedding.l2_distance(query_vector))` returning the exact historical agentic interactions contextualizing the problem.

#### Step 3. Local Verification Run (`cli_demos.py`)

- We'll execute a local Python test script proving that:
  1.  The RLM's truncation acts as expected (by attempting to print a 10,000 character string).
  2.  The Modal volume perfectly preserves a newly created file.
