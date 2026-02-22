# Current State Verification & Legacy Cleanup

The project requires a pause on new features (Phase 6+) to address legacy debt (e.g., `src/fleet_rlm/bridge`) and to firmly verify the current capabilities of the RLM engine via the UI.

## 🎯 Defined User Stories for Browser Testing

To ensure the foundation is sound, the following User Stories must be tested end-to-end through the Frontend UI, triggering the FastAPI backend, DSPy Supervisor, Neon DB, and Modal Sandbox.

### Story 1: Modal Volume Introspection

> **As a** Developer using the Chat UI,  
> **I want to** ask the agent to list the files currently stored in my remote Modal Volume,  
> **So that** I can verify the persistent sandbox filesystem is accessible and correctly wired.
>
> _Test Query:_ "Can you execute code to list all the files currently in the /data/workspace Modal Volume?"
> _Expected Result:_ The agent executes Python `os.listdir('/data/workspace')` in the Modal Sandbox, and streams the results back to the UI.

### Story 2: External Link Ingestion & Analysis

> **As an** Orchestrator,  
> **I want to** give the agent a URL to documentation (e.g., Modal docs),  
> **So that** the RLM engine can fetch the content, ingest it, reason over it through trajectories, and deposit the findings into the Neon Evolutive Memory.
>
> _Test Query:_ "Please fetch and analyze the documentation at https://modal.com/docs/guide. Summarize the core concepts and save them to memory."
> _Expected Result:_ The agent writes a script to scrape the URL, processes the text in chunks within the 2000-character safety bound, summarizes the data, uses `search_evolutive_memory` or triggers a memory update, and outputs the final summary to the UI.

## 🧹 Legacy Cleanup (Pending)

- Investigate `src/fleet_rlm/bridge/` and determine migration/deprecation path.
- Remove redundant boilerplate tying old TUI components.
