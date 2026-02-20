# 📚 fleet-rlm Documentation Index

> Master reference for navigating all project documentation artifacts.

---

## 🧠 Concept & Vision

| Document                                                                                                                                                                   | Description                                                      |
| :------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------- |
| [concept.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/concept.md)                          | Vision, core principles, target users                            |
| [codebase_assessment.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/references/codebase_assessment.md) | LOC analysis, what to retain/edit/clean, maintainability metrics |

---

## 🏗️ Architecture

| Document                                                                                                                                                                    | Description                                                       |
| :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------- |
| [architecture.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/architecture.md)                 | High-level Mermaid system component diagram                       |
| [architecture_diagram.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/architecture_diagram.md) | Full system topology, module dependency graph, 7-layer data flow  |
| [user_flow.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/user_flow.md)                       | End-to-end sequence diagrams (chat, RLM delegation, memory query) |
| [frontend_state.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/frontend_state.md)             | Zustand / TanStack Query state flow for the React frontend        |
| [evolutive_memory.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/evolutive_memory.md)         | Neon pgvector memory architecture                                 |
| [rlm_machine.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/rlm_machine.md)                   | RLM state machine and execution loop detail                       |
| [sequence.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/artifacts/sequence.md)                         | WebSocket message sequence diagram                                |
| [ARCHITECTURE_BIBLE.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/references/ARCHITECTURE_BIBLE.md)    | Original architecture reference                                   |

---

## 📊 Comparison Matrices

| Document                                                                                                                                                                                             | Description                                                             |
| :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------- |
| [dspy_alignment_matrix.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/references/dspy_alignment_matrix.md)                       | Parameter-by-parameter mapping of `dspy.RLM` API → fleet-rlm            |
| [industry_comparison_matrix.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/references/industry_comparison_matrix.md)             | 6-column comparison: DSPy, 2× Daytona, Prime Intellect, fleet v0.4/v0.5 |
| [feature_matrix_current_vs_target.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/references/feature_matrix_current_vs_target.md) | Current baseline vs. hyper-advanced target architecture                 |

---

## 📋 Implementation Plans

| Document                                                                                                                                                              | Status  | Description                                     |
| :-------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------ | :---------------------------------------------- |
| [implementation_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/implementation_plan.md) | —       | Master surgical integration plan (all 5 phases) |
| [detailed_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/detailed_plan.md)             | —       | Contextual analysis & detailed roadmap          |
| [phase_1_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_1_plan.md)               | ✅ Done | Infrastructure Scaffolding & Database Schema    |
| [phase_2_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_2_plan.md)               | ✅ Done | Execution Engine & DSPy Tools                   |
| [phase_3_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_3_plan.md)               | 🔜 Next | Upgrading Agent Core (ReAct + RLM)              |
| [phase_4_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_4_plan.md)               | ⏳      | API & Multiplexed WebSockets                    |
| [phase_5_plan.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_5_plan.md)               | ⏳      | Dual-Pane React Frontend                        |

---

## 🔬 Phase Completion Artifacts

| Document                                                                                                                                                                                        | Description                                     |
| :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------- |
| [phase_1_database_schema_artifact.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_1_database_schema_artifact.md) | Phase 1 verification trace + schema definitions |
| [phase_2_tool_registry_artifact.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_2_tool_registry_artifact.md)     | Phase 2 truncation guard + memory tools         |
| [phase_3_pre_walkthrough.md](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/docs/plans/phase_3_pre_walkthrough.md)                   | Phase 3 pre-implementation walkthrough          |
