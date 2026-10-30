# F3 acceptance: one logical artifact capture

## Status

**Offline capture implementation passes the repository suite. F3 completion remains blocked by F2 publication qualification.**

## Implemented evidence

- The Turn path copies host execution evidence, validates the complete artifact set, checks path and size bounds, verifies each SHA-256 against captured bytes, and checks child promotion receipts against the resulting root paths.
- The sorted logical capture retains paths, sizes, hashes, source, and child agent provenance on the stored Turn.
- `artifact_changes` and canonical completion use the capture paths. `SessionRuntime.commit_turn(artifact_paths=...)` avoids a second workspace listing and validates that the inventory matches the capture.
- Invalid or incomplete capture fails the Turn instead of claiming a complete inventory. Replay uses the stored Turn capture and does not reconstruct it from the current workspace.
- Regression coverage checks hash validation, incomplete evidence refusal, post-capture mutation isolation, and the captured-path handoff.

## Remediation update (30 October 2026, evening)

- **Fixed a regression that failed healthy Turns.** The capture treated the evidence producer's
  `complete: false` as invalid, so any Turn with more than 32 artifacts, a file over 64 KB, or
  more than 256 KB of artifacts raised `RuntimeError` *after* the model had finished. The
  producer limits now follow the workspace limits (100 artifacts, 25 MB per file and in total),
  and reviewed gold keeps its own much smaller `MAX_EVIDENCE_BYTES` bound so raising the
  observed-artifact bound does not loosen gold validation.
- **Fixed a trace-ordering defect found by reading a live trace.** The root span recorded
  `execution_evidence` before `cleanup`; MLflow's span sanitizer has a fixed node budget, so a
  41-artifact evidence tree pushed `cleanup` out and it was exported as `[trace size limit]`.
  Cheap scalar facts (route, usage, cleanup) are now recorded before the evidence tree, so
  truncation can only shorten the evidence. Clipping the trace copy instead was rejected because
  it broke `artifact_correctness` scoring against reviewed gold; the suite caught that.
- New coverage: a Turn-level capture test at the full artifact count with a 300000-byte file, a
  producer test that a healthy output set reports `complete: true`, a gold-limit test proving the
  small bound is retained, and an ordering test that fails against the previous field order.
- Live: a 41-artifact Turn (40 files plus a 300000-byte CSV) completed with
  `artifact_capture.complete: true` and the large file's exact size in the capture; the trace
  retained all 41 evidence entries together with `route`, `lm_calls`, and `cleanup`.

Still outstanding: no quiesce/finalize step for managed publishers (F3.1), no explicitly
recorded capture/fence boundary (F3.2), and no separate publication-vs-commit-vs-cleanup status
(F3.3). F3 remains blocked on F2 for a complete live inventory.

## Validation and dependency

The final `make check` passed 1,844 tests with 1 skipped, plus lint, formatting, type checking, docs links, OpenAPI contract, and package build.

## Implementation coordinates and identities

- Capture and canonical handoff: `src/fleet_rlm/engine/turn.py:303-356` and `src/fleet_rlm/engine/turn.py:586-655`; Store persistence and replay data remain in the existing Turn record. `SessionRuntime.commit_turn` validates and returns the captured inventory in `src/fleet_rlm/session.py:148-245`.
- No database migration was added. Capture metadata is stored with the Turn record and does not assert immutable workspace bytes.
- Dependency identity from `uv.lock`: DSPy 3.4.1, MLflow 3.17.2, Daytona 0.224.0.
- Current working-tree delta for `engine/turn.py` is +92/-6 lines. Session has +62/-16 lines shared with F1/F0, so those are not counted again. Current combined coverage report for `engine/turn.py` is 95% statements and 86% branches.

No durable publication receipt or atomic directory commit is implemented because the Daytona rename boundary failed qualification. F3 is therefore not accepted for live complete inventory, and unattended readiness is not established.
