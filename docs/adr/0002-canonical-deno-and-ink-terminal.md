# ADR 0002: Canonical Deno and Ink terminal

- Status: Accepted
- Date: 2026-07-14
- Owners: Fleet RLM backend and terminal client
- Supersedes: ADR 0001 environment-count and custom-TUI-renderer clauses only

## Context

ADR 0001 established the coordinated Turn contract while naming only hermetic
and Daytona execution and treating a custom terminal renderer as rejected. The
working system now needs a real local DSPy path between hermetic tests and the
full Daytona product, and its maintained terminal client needs one renderer and
one transcript projection rather than parallel classic and Ink paths.

The remediation does not change HTTP routes, database schema, or generated
OpenAPI contracts. It clarifies which profiles are canonical, which
capabilities each profile provides, when application resources exist, and
which terminal modules own transport and projection.

## Decision

Canonical Run Environment set: `deno`, `daytona`.

### Run Environment profiles

- `deno` is the reduced-capability local profile. It uses real `dspy.LM`
  models and leaves the interpreter unset so DSPy creates its default
  Deno/Pyodide `PythonInterpreter`. Its host capabilities are Attachment reads
  and Skills. It has no `create_artifact` tool, Artifact Candidate promotion,
  Daytona broker, Sandbox, or Workspace Volume Scope.
- `daytona` is the full Fleet profile. It owns the Daytona Sandbox,
  Workspace Volume Scope, durable Attachment staging, Artifact Candidate
  promotion, and Turn Commit publication.

These are explicit selections, not fallback levels. A selected profile fails
closed when its required runtime or credentials are absent.

### Lifespan-only composition

`fleet_rlm.app.create_app()` constructs only the FastAPI/router shell and empty
state. The FastAPI lifespan installs exactly one complete profile inventory,
marks it ready only after successful wiring, and clears it during shutdown or
failed startup.

`fleet_rlm.composition` owns profile wiring. `install_deno_composition()` uses
the shared local inventory builder; `install_daytona_composition()` and
`dispose_daytona_composition()` own Daytona startup, rollback, and shutdown.
Private tests use `composition.testing` without adding a public profile. The local lifespan may own a database engine
and Session factory, but calls `create_tables` only for SQLite and disposes any
engine it created. Routes only retrieve lifespan-composed modules.

`fleet_rlm.chat.deno_run_environment` owns Deno's in-process sinks,
environment provider, reduced capability preparation, and RLM factory. It
passes `interpreter=None`; Fleet does not provide a substitute Deno
interpreter implementation.

### One terminal renderer and projection path

Ink is the only maintained terminal renderer. The classic renderer,
`--classic`, `start:classic`, and `@ai-sdk/tui` are removed without a
compatibility period.

Terminal module ownership is strict:

- `tools/fleet-tui/src/fleet-turn-stream.ts` owns request opening, the bounded
  same-key retry policy, and the strict UI SSE stream lifecycle.
- `tools/fleet-tui/src/sse.ts` owns SSE frame parsing and closed validation of
  generated `FleetUIMessageChunk` values.
- `tools/fleet-tui/src/tui/projection.ts` owns both live chunk projection and
  durable Turn projection through `LiveTurnProjector.push()` and
  `projectDurableTurns()`.
- `tools/fleet-tui/src/tui/store.ts` owns atomic Session hydration. Reload
  replaces the prior Session transcript as one state transition rather than
  replaying a second display protocol.

Live and reload paths must produce the same display semantics for every
supported durable part. Unknown chunks fail closed. Structured results are
projected as typed Result cards in both paths and may merge an accompanying
assistant narrative without changing the durable wire contract.

The maintained presentation is an achromatic operator timeline. Reasoning,
code, interpreter output, tools, recoverable errors, results, and usage remain
chronological; execution cards start expanded and can be collapsed
individually. White and gray hierarchy, glyphs, weight, rules, and border
density communicate state without semantic color.

## Consequences

- Deno is continuously tested as a real runtime dependency but is not presented
  as Daytona feature parity.
- Normal fast test lanes exclude Deno-runtime tests; a required pinned-Deno CI
  job runs them explicitly.
- Application resources cannot be constructed at import time or by routes and
  cannot survive outside the FastAPI lifespan that owns them.
- Terminal transport, projection, and storage each have one owner, so live and
  reload drift is contract-testable without keeping a second renderer.
- A successful structured submission is visible as a Result card rather than
  being inferred from interpreter output or duplicated assistant text.
- The coordinated Turn, persistence, Attachment, Artifact, DSPy, and generated
  contract decisions in ADR 0001 remain in force.

## Validation

Implementation requires focused lifespan/composition tests, Deno-runtime
contracts, strict stream and live/reload parity tests, the pinned terminal
checks, documentation drift checks, and the normal repository quality gate.

## Rejected alternatives

- Keep only hermetic and Daytona: leaves local real-DSPy development coupled to
  the full provider environment.
- Give Deno Daytona-equivalent Artifact behavior: creates a false durability
  promise without Workspace Volume Scope and promotion.
- Keep classic as a fallback renderer: preserves two stream/projection paths
  and makes semantic parity an ongoing synchronization problem.
- Render structured results through a second terminal-only parser: would create
  a competing display contract and reintroduce live/reload divergence.
