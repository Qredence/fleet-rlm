# Fleet RLM Recursive Runtime

Current phase: Phase 1 — Native DSPy context capsule, RLM streaming, and co-located Daytona REPL
ExecPlan: .scratch/fleet-rlm-recursive-runtime/phase-01-dspy-context-daytona-repl.md
Status: in progress

## Current exit gate

- [x] Daytona executes persistent Python cells and host callbacks in one process namespace.
- [x] A Volume-backed `dspy.SandboxSerializable` capsule injects authorized immutable context.
- [x] Prepared and sandbox-accessed attachments project through the existing bounded event.
- [x] Stock `dspy.RLM` uses native `dspy.streamify` and `dspy.streaming.StreamListener` for Root reasoning/code.
- [x] First DSPy stream deltas reach FastAPI SSE before typed `Prediction` completion and `[DONE]` remains last.
- [x] `fleet-tui` appends incremental reasoning/code chunks to stable cards and accepts canonical corrections without duplicates.
- [x] Provider/cache no-stream fallback still completes through typed `Prediction`.
- [x] Explicit live commands use the TOML `runtime.live_enabled` policy; `false` fails closed before provider access.
- [ ] One explicit DeepSeek/Daytona canary records stream timing, typed completion, and cleanup evidence when run.
- [ ] Focused, full, security, release, docs, and diff validation pass.
- [ ] Phase validation and retrospective are complete.

## Phase frontier

- [x] Phase 0 — Evidence, DSPy baseline, and Daytona feasibility (closed on negative callback evidence)
- [ ] Phase 1 — Native DSPy context capsule, RLM streaming, and co-located Daytona REPL
- [ ] Phase 2 — DSPy recursive children
- [ ] Phase 3 — Dependency DAG and verified completion
- [ ] Phase 4 — Commit-gated derived state
- [ ] Phase 5 — Bounded SSE, replay, and TUI graph
- [ ] Phase 6 — DSPy evaluation, GEPA, promotion, and legacy removal
