# Fleet RLM backend architecture

## Runtime flow

```text
HTTP POST /api/chat
  -> identity and Attachment validation
  -> TurnCoordinator
  -> durable Session History + Checkpoint claim
  -> Daytona Sandbox with Workspace Volume Scope
  -> fresh DSPy RLM and CodeInterpreter
  -> host-mediated Skill, Attachment, and Artifact Candidate tools
  -> candidate byte promotion
  -> atomic Turn/Run/Checkpoint/Artifact metadata commit
  -> artifact.created* then one run.completed terminal
  -> Interpreter Lease release
```

Failures after streaming begins are sanitized Runtime Events. A failed Turn
Commit emits exactly one error terminal, advances no history, publishes no
Artifact identity, and still releases the Interpreter Lease.

## Ownership

- FastAPI lifespan owns database engines, Daytona clients, gateways,
  repositories, and shutdown.
- Routes retrieve already-composed modules from `api/dependencies.py`.
- `RLMRunner` executes one fresh DSPy RLM per Turn.
- `TurnCoordinator` owns commit, public terminal ordering, and lease release.
- `daytona/` is the only Daytona SDK import boundary.
- `persistence/` implements domain repository interfaces; Alembic owns the live
  schema.

## Durable files

Attachment bytes are written to Workspace Volume Scope before metadata.
Referenced Attachments are staged into the Run Sandbox. Artifact Candidates are
private Run outputs until promoted to UUID-unique durable paths and committed
with Turn metadata. Failed metadata commits may leave GC-eligible orphan bytes,
never public Artifact rows.

## Compatibility

There is no legacy backend, `/api/v1`, WebSocket execution, dual-serve, or data
migration layer. Frontend adaptation is a separate effort.

## Cutover status

The RLM-native package replaced the former Python backend after its attachment,
Artifact, persistence, and Workspace-isolation exit bar passed. The maintained
architecture is the module ownership described above and in
[`reference/codebase-map.md`](reference/codebase-map.md); superseded migration
plans remain available through Git history rather than a parallel docs tree.
