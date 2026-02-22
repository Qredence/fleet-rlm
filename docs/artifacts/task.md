# Fleet-RLM Next Evolution Tasks

- [ ] **Phase A: Legacy Cleanup & Current State Verification**
  - [ ] Investigate and prune legacy directories (e.g., `src/fleet_rlm/bridge`)
  - [ ] Define user stories for Modal Volume and Sandbox verification
  - [ ] Define user story for external link ingestion and RLM trajectory analysis
  - [ ] Run browser subagent to test Story 1: Modal Volume Introspection
  - [ ] Run browser subagent to test Story 2: External Link Ingestion

---

- [ ] Implement Supervisor Agent that delegates to specialized Sub-Agents
- [ ] Add advanced DSPy tools (e.g., web scraping, structural dataset analysis)
- [ ] Refine the Multi-Agent state handoff in `modal_repl.py`
- [ ] Verify: Create unit tests for Sub-Agent delegation logic

- [ ] **Phase 7: Frontend Workspace Polish & Memory Visualization**
  - [ ] Enhance Dual-Pane UI with native code-block execution visualization
  - [ ] Implement dynamic Mermaid.js graph rendering for agent taxonomy/memory flows
  - [ ] Refine Zustand state for real-time memory reflection updates
  - [ ] Verify: End-to-end component rendering tests for memory UI blocks

- [ ] **Phase 8: Evolutive Memory Hardening**
  - [ ] Implement "Reflection Loop" cron/background task to consolidate redundant memories
  - [ ] Add `hybrid search` (Full Text Search + pgvector) to Neon DB for better recall
  - [ ] Fine-tune the context truncation threshold based on dynamic input size
  - [ ] Verify: Integration test ensuring memory consolidation improves retrieval latency
