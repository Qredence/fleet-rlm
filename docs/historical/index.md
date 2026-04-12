# Historical Snapshots

Archived audits and codebase snapshots that are kept for reference but are not the source of truth for current implementation details.

## Current Sources of Truth

- [Architecture Overview](../architecture.md)
- [Current Architecture and Transition Note](../notes/current-architecture-transition.md)
- [Reference Index](../reference/index.md)
- [Explanation Index](../explanation/index.md)
- Local planning documents such as `PLANS.md` and `TASKS.md` when present in the repo root.

## Historical References

- [Architecture Audit (2026-03-06)](architecture-audit-2026-03-06.md)
- [Code Health Complexity Heatmap](code-health-complexity-heatmap.md)
- [Code Health Map](code-health-map.md)
- [Code Health Unused Review](code-health-unused-review.md)

## Architecture and Migration History

These notes explain how the repo moved from a thicker websocket/orchestration surface toward the current transport/host/runtime split.

- [Phase 1: Worker Boundary Extraction](../notes/phase-1-worker-boundary.md)
- [Phase 2: Websocket Transport Thinning](../notes/phase-2-ws-thinning.md)
- [Phase 3: Orchestration Seams](../notes/phase-3-orchestration-seams.md)
- [Phase 4: Minimal Outer Orchestration Layer](../notes/phase-4-outer-orchestration.md)
- [Phase 5: Session Orchestration](../notes/phase-5-session-orchestration.md)
- [Phase 6: Terminal Orchestration](../notes/phase-6-terminal-orchestration.md)
- [Phase 7/8: Agent Framework Transition](../notes/phase-7-8-agent-framework-transition.md)
- [Phase 9: Agent Host HITL Migration](../notes/phase-9-agent-host-hitl-migration.md)
- [Phase 10: Agent Host Session Migration](../notes/phase-10-agent-host-session-migration.md)
- [Phase 11: Agent Host REPL Bridge Migration](../notes/phase-11-agent-host-repl-bridge.md)
- [Phase 12: DSPy Recursive Module + GEPA](../notes/phase-12-dspy-recursive-module-gepa.md)
- [Phase 13: Recursive Context Assembly](../notes/phase-13-recursive-context-assembly.md)
- [Phase 14: Recursive Decomposition Module](../notes/phase-14-recursive-decomposition-module.md)
- [Phase 15: Recursive Verification Module](../notes/phase-15-recursive-verification-module.md)
- [Phase 17: Recursive Repair Module](../notes/phase-17-recursive-repair-module.md)
