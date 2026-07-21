# Fleet RLM backend architecture

Canonical Run Environment set: `deno`, `daytona`.

## Runtime flow

```text
POST /api/sessions/{session_id}/turns + Idempotency-Key
  -> deterministic local scope and Turn input validation
  -> Attachment ownership and exact Skill selection validation
  -> TurnCoordinator.open()
     -> TurnLifecycle.begin(): replay or atomic Run claim
     -> TurnPreparationModule.prepare(): context, tools, environment resources
  -> RLMRunner: one fresh native dspy.RLM and interpreter context
  -> Runtime Events from native trajectory, interpreter, and host-tool boundaries
  -> TurnLifecycle.finish()
     -> validate typed result and private snapshot
     -> promote Artifact Candidate bytes (Daytona only)
     -> atomic Turn/Run/Checkpoint/Artifact commit or failure settlement
  -> artifact.created* then exactly one run.completed terminal
  -> TurnCoordinator cleanup and Interpreter Lease release
```

Preparation failures remain ordinary safe HTTP outcomes. Failures after headers
emit exactly one sanitized terminal event. A failed commit advances no Session
history, publishes no Artifact identity, and still releases owned resources.

Within one Run, interpreter calls reuse one context so Python state persists
across RLM iterations. Every later Run receives a fresh context. Replacing a
Daytona Sandbox remounts the Workspace Volume Scope but does not preserve Python
globals.

Every Signature receives request text, bounded `session_context`, bounded
`skill_cards`, and bounded Attachment metadata. Full committed history remains
host-side behind `read_session_history`.

## Composition and ownership

- `app.create_app()` creates the FastAPI application, installs handlers and
  routers, and eagerly constructs the immutable bundled Skill catalog.
- FastAPI lifespan validates settings and installs exactly one complete Deno,
  Daytona, or explicitly injected private-test runtime inventory. It owns
  startup rollback and shutdown.
- `composition/common.py`, `deno.py`, `daytona.py`, and `testing.py` own runtime
  wiring. A locally owned database engine creates tables only for SQLite.
- Routes retrieve composed runtime modules through `api/dependencies.py`; the
  Skills discovery route may recreate only its static in-memory catalog fallback.
- `TurnPreparationModule` owns ordered validation, environment acquisition,
  bounded context, Tool construction, and reverse-order rollback.
- `RLMRunner` executes one fresh DSPy RLM and emits no terminal event.
- `TurnLifecycle.finish()` owns private result snapshots, Artifact publication,
  atomic Turn Commit, and durable failure settlement.
- `TurnCoordinator` owns stream orchestration, heartbeat coordination, terminal
  ordering, and final cleanup.
- `daytona/` is the exclusive Daytona SDK boundary.
- `persistence/` implements domain repository interfaces; Alembic owns the live
  schema.

## Skills

The bundled catalog contains `long-context`, `workspace-files`,
`data-analysis`, and `report-builder`. Skill disclosure is progressive: bounded
Cards are available at startup, a full `SKILL.md` loads only when invoked or
exactly preselected, and declared resources load only after the Skill body.
`data-analysis` is the only bundled Skill that supplies a custom validated DSPy
Signature; `report-builder` is instruction-only.

Skill Markdown and resources cannot register host tools. Runtime composition
owns the fixed core tools plus exactly `load_skill` and
`read_skill_resource`. HTTP requests provide only Skill identity/version
selections. Selection is resolved synchronously against the immutable catalog;
at most one selected Skill may provide a validated DSPy Signature.

Host tool event views expose bounded allowlisted metadata. A Tool without a
declared view exposes identity, name, status, and a fixed failure message only.
Provider and transport failures use closed public messages rather than raw
exception text.

## Runtime profiles

- Deno uses real `dspy.LM` roles and DSPy's default Deno/Pyodide interpreter.
  It supports Attachment reads and Skills, but has no Daytona broker, Session
  Workspace tools, `create_artifact`, or durable Artifact promotion.
- Daytona owns Sandbox/Interpreter Leases, Workspace Volume Scope, durable
  Attachment staging, Session Workspace files, private result snapshots, and
  Artifact Candidate promotion.
- Profiles are explicit and fail closed when prerequisites are absent. Private
  deterministic testing composition is not a public fallback profile.

## Terminal client

`@earendil-works/pi-tui@0.80.10` is the only renderer. The client requires Node
22.19+, owns no model or provider runtime, and consumes the FastAPI HTTP/SSE
contract.

`fleet-turn-stream.ts` owns strict request/retry/stream lifecycle; `sse.ts` owns
framing and generated-chunk validation; `tui/runner.ts` owns active Run and
cancellation control; `projection.ts` owns live/durable parity; `store.ts` owns
atomic hydration. The application, screen, message renderer, commands,
presenters, and autocomplete own interaction and static presentation.

The operator timeline renders all evidence fully expanded in native terminal
scrollback. Fleet does not capture the mouse, clip old messages, or maintain a
transcript viewport. Artifact CLI downloads validate content length and SHA-256
before atomically replacing the requested destination.

## Durable files

Attachment bytes are written to Workspace Volume Scope before metadata and are
staged for referenced Runs. Artifact Candidates are private Run outputs until
verified bytes reach UUID-unique durable paths and their metadata commits with
the Turn. Failed metadata commits may leave GC-eligible orphan bytes, never
public rows.

Session Workspace files are immediate private state under the Session Volume
path. They survive failed Runs and Sandbox replacement independently of the
commit-gated result snapshot and Artifact lifecycle.

## Compatibility and status

There is no legacy backend, `/api/v1`, WebSocket execution, dual-serve, data
migration layer, classic terminal renderer, or maintained Web frontend. A
future graphical client is a separate effort. The current module ownership is
also summarized in the [codebase map](reference/codebase-map.md).
