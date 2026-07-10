# Frontend SSE and legacy cleanup dossier

## Phase 10 — Frontend SSE client and legacy cleanup

- **Order:** `10`
- **Status:** `planned`
- **Track:** `Frontend`
- **Summary:** Move workspace chat to `/api/chat` SSE and remove legacy paths only after measured confidence.

### Goal and stable interfaces

The Vite/React workspace uses `/api/chat` for transcript execution. AI Elements
render AI SDK UIMessage text, reasoning, tool, artifact, task, performance, and
span parts. Trace/debug panels consume backend `mapped_render_kind` and
`mapped_component_type` values.

WebSocket remains for terminal, sandbox, and other bidirectional control. Legacy
chat execution is deprecated only after telemetry, browser smoke, contract tests,
session restore, and promotion evidence show the SSE path is safe.

### Prerequisites

- Phase 6 trace, redaction, performance, and transport evidence is complete.
- Phase 7 configuration preserves a server-owned backend selector and rollback path.
- Phase 8 quality work remains offline and does not couple frontend migration to GEPA.
- Phase 9 has promoted direct RLM with passing live golden-flow, safety, trace,
  session, artifact, and performance evidence.

### Ordered internal stages

1. **Adopt SSE for workspace transcript execution.** Keep WebSocket controls
   available and prove transcript, session restore, attachment, artifact, and
   trace/debug behavior through browser and contract tests.
2. **Narrow WebSocket responsibility.** Inventory consumers and retain only
   terminal, sandbox input, cancellation, resize, or other genuinely
   bidirectional operations.
3. **Inventory legacy runtime consumers.** Map imports, configuration,
   compatibility exports, tests, operational rollback requirements, and any
   external consumers before deprecation.
4. **Deprecate behind a rollback window.** Direct RLM and SSE remain default
   while telemetry and live smoke prove that rollback can be exercised without
   exposing backend choice to untrusted clients.
5. **Perform evidence-backed cleanup.** Move retained adapters into explicit
   compatibility ownership and delete only branches proven dead by consumer,
   contract, browser, telemetry, and rollback evidence.

### Non-goals

- Remove WebSocket terminal or sandbox control.
- Delete legacy runtime branches before Phase 9 promotion evidence.
- Reimplement backend render classification in the frontend.

### Acceptance criteria

- [ ] Workspace chat uses `/api/chat` SSE by default.
- [ ] AI Elements render every supported UIMessage and Fleet data part.
- [ ] Session restore, attachments, artifacts, and trace/debug panels remain compatible.
- [ ] WebSocket is limited to bidirectional control responsibilities.
- [ ] Legacy execution moves to compatibility ownership before unused branches are removed.
- [ ] Browser, contract, telemetry, and rollback evidence support each deletion.
- [ ] Each internal stage records its prerequisites, evidence, rollback trigger,
  and surviving compatibility surface before the next stage begins.

### Rollback and evidence

- SSE adoption rolls back by restoring the prior workspace transport consumer;
  it does not expose `execution_backend` on `ChatRequest`.
- Runtime rollback uses the server-owned compatibility backend only while its
  active contract remains documented and tested.
- A deletion is blocked when any active import, client, browser flow, operational
  runbook, or rollback test still depends on it.
- Evidence includes frontend unit/E2E results, backend HTTP/WebSocket contracts,
  session restore and attachment/artifact flows, trace/performance telemetry,
  consumer inventories, and an explicit deletion ledger.

### Validation

```bash
pnpm --dir src/frontend run type-check
pnpm --dir src/frontend run test:unit
pnpm --dir src/frontend run test:e2e
```
