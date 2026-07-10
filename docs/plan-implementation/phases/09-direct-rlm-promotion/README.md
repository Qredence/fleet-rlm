# Direct RLM promotion dossier

## Phase 9 — Direct RLM default promotion

- **Order:** `9`
- **Status:** `promotion_gated`
- **Track:** `Runtime`
- **Summary:** Make direct RLM the default only after live golden-flow, parity, safety, and performance evidence.

### Preconditions

Direct RLM must handle simple chat, Daytona file operations, selected Skills,
attachments, artifact creation/readback, resumed sessions, runtime-event and
trace-debug parity, optional MLflow spans, and configured live Daytona/LLM runs.
The legacy runtime and WebSocket compatibility remain available during rollout.

### Required live matrix

Run `scripts/validate_rlm_e2e_trace.py --promotion-gate` against two distinct
local servers configured for `legacy_agent_runtime` and `direct_rlm`. Each backend
runs the same three-turn workload. Evidence must include:

- selected trusted `long-context` Skill;
- uploaded sentinel attachment passed by ID only;
- one resumed session across all three turns;
- a session-scoped Markdown artifact created and read back with marker/checksum;
- WebSocket and execution streams plus session trace-debug evidence;
- a trace verified against explicitly enabled MLflow;
- duration, tokens, fallback/degraded flags, terminal errors, and median comparison.

The matrix fails on terminal error, fallback/degradation, missing token evidence,
or duration/token regression beyond the configured threshold. Passing is a
prerequisite, not an automatic configuration mutation.

### Non-goals

- Flip `AppConfig.execution_backend` from documentation or unit evidence alone.
- Remove the legacy fallback or WebSocket compatibility path.
- Expose backend choice on `ChatRequest`.

### Acceptance criteria

- [ ] Both local backend servers complete the three-turn promotion workload.
- [ ] Direct RLM proves Skills, attachment, session, artifact, trace, and MLflow behavior.
- [ ] No terminal errors, fallbacks, degradation, or material performance regression occur.
- [ ] Evidence is recorded before the default changes.
- [ ] Legacy configuration remains available during rollout.

### Evidence

- [Direct RLM promotion matrix](evidence-promotion-matrix.md)

### Validation

```bash
uv run python scripts/validate_rlm_e2e_trace.py --promotion-gate \
  --legacy-server-url http://127.0.0.1:8000 \
  --direct-server-url http://127.0.0.1:8001
```
