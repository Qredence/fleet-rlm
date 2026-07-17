# Fleet RLM backend architecture

Canonical Run Environment set: `deno`, `daytona`.

## Runtime flow

```text
HTTP POST /api/sessions/{session_id}/turns + Idempotency-Key
  -> deterministic local scope, Turn input, Attachment validation, and optional exact Skill selections
  -> TurnCoordinator
  -> durable Session History + atomic Run claim
  -> Run environment selected by FLEET_RUN_ENVIRONMENT
     - deno:     real dspy.LM, DSPy default PythonInterpreter (Deno/Pyodide WASM), in-process sinks
     - daytona:  Daytona Sandbox with Workspace Volume Scope
  -> fresh DSPy RLM and CodeInterpreter
  -> all authorized visible Skill summaries in the primary RLM input
  -> progressive Skill body/resource, Attachment, and Artifact Candidate tools
     (deno: read_attachment + skills only; no create_artifact or promotion)
  -> candidate byte promotion (daytona only; deno skips durable volume write)
  -> atomic Turn/Run/Checkpoint/Artifact metadata commit
  -> artifact.created* then one run.completed terminal
  -> Interpreter Lease release
```

Failures after streaming begins are sanitized Runtime Events. A failed Turn
Commit emits exactly one error terminal, advances no history, publishes no
Artifact identity, and still releases the Interpreter Lease.

Within one Daytona Run, repeated interpreter calls reuse one explicit context,
so Python state persists across RLM iterations. Every later Run receives a
fresh context. Replacing a Sandbox remounts the same Workspace Volume Scope but
does not preserve interpreter globals.

Host capability registries normalize compatibility callables into explicit
`dspy.Tool` objects before Turn composition. Final Turn blueprints are
Tool-only and associate host-owned event views by Tool name. A declared view
exposes bounded allowlisted metadata; a Tool without one exposes only its call
identity, name, status, and fixed failure message. Explicit RLM reasoning,
generated code, and interpreter output are preserved verbatim up to the Run's
`max_output_chars` bound. Provider and transport failures use closed public
messages rather than provider exception text.

Skill disclosure is three-level: visible names and descriptions are available
at Turn startup, a full `SKILL.md` loads only when invoked (or explicitly
preloaded by exact pinned selection), and a declared resource loads only after
the Skill body. Host executable capabilities remain a separate registry and
can be composed only during preparation for an explicit selection; Skill
Markdown and Python resources cannot register host tools.

## Ownership

- `app.create_app()` constructs only the FastAPI/router shell and empty state.
- FastAPI lifespan installs exactly one complete Run Environment inventory,
  marks it ready after successful wiring, and owns shutdown or startup rollback.
- `composition/` owns explicit Daytona, Deno, common, and private testing wiring.
  Runtime installers share one inventory builder; Daytona owns live resources. A locally owned
  database engine creates tables only for SQLite and is disposed by lifespan.
- Routes retrieve already-composed modules from `api/dependencies.py`.
- `RLMRunner` executes one fresh DSPy RLM per Turn.
- `TurnCoordinator` owns commit, public terminal ordering, and lease release.
- `chat/deno_run_environment.py` owns Deno's in-process sinks, reduced
  capabilities, and RLM factory. Passing no interpreter delegates to DSPy's
  default Deno/Pyodide interpreter.
- `daytona/` is the only Daytona SDK import boundary.
- `persistence/` implements domain repository interfaces; Alembic owns the live
  schema.

## Terminal client

Ink is the sole renderer; there is no classic compatibility path.
`fleet-turn-stream.ts` owns request/retry and the strict UI SSE lifecycle,
`sse.ts` owns frame parsing and closed generated-chunk validation,
`tui/projection.ts` owns both live and durable projection, and `tui/store.ts`
owns atomic Session hydration. Live and reload therefore share display
semantics. Structured results project into the same typed Result card in both
paths; assistant narrative may merge into that card without changing the SSE
or durable UI-message contracts. Ink renders the execution timeline with one
achromatic white-and-gray theme.

## Durable files

Attachment bytes are written to Workspace Volume Scope before metadata.
Referenced Attachments are staged into the Run Sandbox. Artifact Candidates are
private Run outputs until promoted to UUID-unique durable paths and committed
with Turn metadata. Failed metadata commits may leave GC-eligible orphan bytes,
never public Artifact rows.

Session Workspace files are immediate private state under the Session's Volume
path. They survive failed Runs, later Turns, and Sandbox replacement independently
of the commit-gated result snapshot and Artifact lifecycle. The opt-in
[Daytona MVP proof](how-to-guides/dspy-integration.md#run-the-complete-daytona-proof)
verifies this boundary through the real FastAPI and native DSPy path.

## Compatibility

There is no legacy backend, `/api/v1`, WebSocket execution, dual-serve, data
migration layer, or classic terminal renderer. A graphical frontend is a
separate effort.

## Cutover status

The RLM-native package replaced the former Python backend after its attachment,
Artifact, persistence, and Workspace-isolation exit bar passed. The maintained
architecture is the module ownership described above and in
[`reference/codebase-map.md`](reference/codebase-map.md); superseded migration
plans remain available through Git history rather than a parallel docs tree.
