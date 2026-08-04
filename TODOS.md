# Fleet RLM Recursive Runtime

Current phase: Phase 1 and Phase 2 live-evidence closure
ExecPlan: .scratch/fleet-rlm-recursive-runtime/phase-01-dspy-context-daytona-repl.md
Phase 2 ExecPlan: .scratch/fleet-rlm-recursive-runtime/phase-02-dspy-recursive-children-daytona.md
Status: implementation and offline/repository validation complete; credentialed canaries and receipts pending

## Phase 1 progress and exit gate

- [x] Daytona executes persistent Python cells and host callbacks in one process namespace.
- [x] A Volume-backed `dspy.SandboxSerializable` capsule injects authorized immutable context.
- [x] Prepared and sandbox-accessed attachments project through the existing bounded event.
- [x] Stock `dspy.RLM` uses native `dspy.streamify` and `dspy.streaming.StreamListener` for Root reasoning/code.
- [x] First DSPy stream deltas reach FastAPI SSE before typed `Prediction` completion and `[DONE]` remains last.
- [x] `fleet-tui` appends incremental reasoning/code chunks to stable cards and accepts canonical corrections without duplicates.
- [x] Provider/cache no-stream fallback still completes through typed `Prediction`.
- [x] Explicit live commands use the TOML `runtime.live_enabled` policy; `false` fails closed before provider access.
- [x] The focused Phase 1 lane, `make check`, security, release, docs, and diff gates pass; the full check reached 81.06% coverage.
- [x] The Phase 1 canary and policy-gated receipt verifier are implemented, with focused unit coverage for fail-closed policy, receipt validation, output-path safety, and inherited environment preservation.
- [ ] Run one explicitly authorized DeepSeek/Daytona Phase 1 canary against a clean committed candidate and record its sanitized receipt.
- [ ] Reconcile the Phase 1 receipt and retrospective, then close the phase.

## Phase 2 progress and exit gate

- [x] `daytona-recursive` enables recursion explicitly; normal `daytona` exposes neither `rlm_query` nor recursive Root guidance.
- [x] Each real Root delegation creates one dedicated ephemeral Daytona child Sandbox running a native DSPy RLM with normal network egress.
- [x] The child shares only the Daytona Volume ID, mounted at `recursive/<workspace-id>/<run-id>/<call-index>`; it cannot mount the Root workspace scope or receive Fleet capabilities, credentials, Attachments, Artifacts, history, or broker state.
- [x] Strict child cleanup shuts down its interpreter, removes only files in its child scope, deletes only its child Sandbox, and blocks Root success when cleanup fails.
- [x] Root continuity, depth fallback without a grandchild, structural-only child tracing, policy gating, cleanup failure settlement, and child mount isolation are covered offline.
- [x] The Phase 2 live canary, sanitized receipt verifier, operator documentation, and focused verifier tests are implemented. Focused tests, `make check`, security, release, docs, direct Ruff/compile checks, and `git diff --check` pass.
- [ ] After Phase 1 has a committed passing receipt and live execution is explicitly authorized, run the Phase 2 canary against a clean committed candidate and record its sanitized receipt.
- [ ] Reconcile the Phase 2 receipt and retrospective, then close the phase and begin the Phase 3 dependency-DAG design.

## Phase frontier

- [x] Phase 0 — Evidence, DSPy baseline, and Daytona feasibility (closed on negative callback evidence)
- [ ] Phase 1 — Native DSPy context capsule, RLM streaming, and co-located Daytona REPL (implementation and offline validation complete; live receipt pending)
- [ ] Phase 2 — DSPy recursive children (implementation and offline validation complete; Phase 1-gated live receipt pending)
- [ ] Phase 3 — Dependency DAG and verified completion
- [ ] Phase 4 — Commit-gated derived state
- [ ] Phase 5 — Bounded SSE, replay, and TUI graph
- [ ] Phase 6 — DSPy evaluation, GEPA, promotion, and legacy removal
