# Comparison Matrix: Initial vs. Planned `fleet‑rlm`

| Aspect | Initial `fleet‑rlm` (GitHub snapshot) | Planned `fleet‑rlm` (Final Plan) |
|--------|----------------------------------------|----------------------------------|
| **Architecture** | Dual‑mode: frontend either chats directly or delegates to a DSPy RLM sandbox. Complex branching logic. | **Single‑mode, one session, one sandbox.** The agent inside the sandbox handles all interactions; the backend just forwards messages. No routing complexity. |
| **State & Memory** | Stateless beyond a single session. `memory_tools.py` exists but is over‑engineered and not tied to a persistent store. | **Stateful via persistent Daytona Volume.** Memory, knowledge, and skills survive sandbox restarts and accumulate across sessions. |
| **Codebase Complexity** | “Overcharged” with unused chunking strategies, EPUB/PPT ingestion paths, speculative tools (`buffer_tools`, `preview.py`), and a sprawling `config.yaml`. | **Lean.** ~30% fewer lines. Dead code removed, config trimmed to essentials, tools deactivated until rebuilt cleanly. |
| **DSPy Usage** | `FleetAgent` wraps a `dspy.ReAct` agent; uses RLM only when heavy work is delegated. No unified escalation. | **Unified DSPy Module.** Lightweight `ChainOfThought` for simple replies, automatically escalates to `dspy.RLM` with full tools when needed. |
| **Tool Set** | Coding tools (`edit_file`, `apply_patch`), document loading, delegation (`rlm_delegate`, `rlm_delegate_batched`). No web search or memory tools active. | **Expanded, essential‑only tool set.** Adds `web_search`, `fetch_page`, `search_knowledge`, `remember`, `recall`, `load_skill`. All wrapped as `dspy.Tool`. |
| **Web Research** | Not supported (only `load_document` for local/HTTP files). | **Fully supported.** Agent can search the web, fetch pages, extract clean text, and store findings in the knowledge base. |
| **Document Analysis** | Ingestion via MarkItDown with many fallbacks; no persistent storage. | **Persistent document analysis.** Ingested docs saved to volume, indexed for later retrieval. |
| **Memory Persistence** | `memory_tools.py` present but deactivated. Would lose state on sandbox teardown. | **SQLite on volume.** `remember`/`recall` tools read/write a shared, persistent memory store. |
| **Skills System** | Only a bundled `rlm‑long‑context` skill; no loading mechanism. | **Human‑curated skill library.** `load_skill` reads markdown files from volume. Skills are easily added without code changes. No autonomous creation yet. |
| **Child RLM Isolation** | Child sandboxes clone the repo but have no shared state or volume. | **Volume subpath isolation.** Children get read‑only access to shared memory/skills and a private workspace, preventing interference. |
| **Concurrency Control** | No limit; could spawn arbitrary numbers of sandboxes. | **Global cap of 5 sandboxes** (root + children) enforced by `asyncio.Semaphore`. Prevents cost runaway. |
| **Observability** | Minimal logging, possibly some LiteLLM callbacks. | **MLflow tracking for every RLM run.** Parameters, metrics, trajectories, and artifacts logged automatically. |
| **Session Continuity** | Session state lost if sandbox stops; frontend can’t seamlessly resume. | **Full resume.** Sandbox stops/restarts with same volume; agent restores conversation summary and memory. |
| **Context Management** | Likely passes raw conversation history; risks context‑window bloat. | **Structured memory retrieval.** Agent stores facts and retrieves only relevant ones; conversation summary is compressed, not raw history. |
| **Daytona Volumes** | Volume concept exists in config but unused. | **Core of the architecture.** Volume is the brain; mounted in every sandbox with proper subpath isolation for children. |
| **Configuration** | `config.yaml` bloated with many advanced, unused keys. | **Minimal, user‑facing config.** Only `llm`, `sandbox`, `volume`, and `rlm_settings`. Advanced options moved to reference file. |
| **Effort to Implement** | — | **22–30 days** of focused work, each phase independently shippable. |

**Summary:** The planned system transforms `fleet‑rlm` from a stateless, code‑heavy coding‑oriented agent into a **lean, stateful, multi‑tasking recursive agent** that handles reasoning, research, and coding equally well—while using Daytona and DSPy exactly as they were designed.