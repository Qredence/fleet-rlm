# Revision 2 - applied PRD amendments

**Date:** 29 October 2026. **Basis:** the supplied original PRDs and `PRD-REVIEW-DECISIONS.md`. **Nature:** direct editorial/design updates, not code implementation, fresh source verification or a test run.

The earlier reviewed ZIP kept the individual PRDs unchanged. This release edits **all seven PRDs and their README**. The review source is included unchanged under `references/` for provenance; the revised PRDs are the active instructions.

## Changes by slice

| Document | Applied change | Compatibility / scope consequence |
|---|---|---|
| [A](A-native-dspy-integration.md) | Recorded gateway corpus and fallback-budget gate before adapter deletion; native-first shim only when demonstrated necessary; one existing selection owner; no claim timeout disables all retries | Context/batch fixes can ship independently; deletion is not declared compatible without corpus evidence |
| [B](B-canonical-turn-result.md) | One field inventory/canonical result; existing idempotency readback first; no general recovery service/status hierarchy; F3 remains a later snapshot handoff | Canonical result can ship before filesystem redesign; old rows and unresolved commits retain existing semantics |
| [C](C-fastapi-contracts.md) | Native response models and validated SSE examples first; mandatory vendor extension/generator normalization removed; optional extension requires a real consumer test; native type constraints tested before extra validators | No compulsory OpenAPI-version migration or second documentation protocol; current clients and old rows remain the compatibility gate |
| [D](D-optimizer-ownership.md) | Worker-only stage/progress writer; API process/cancel/exit ownership retained; required-persistence latch stays local; optional telemetry distinguished; post-compile failures tested | One writer per responsibility, not one writer for the entire record; no new failure-state service |
| [E](E-native-rollout-state.md) | Blocking native parity/copy spike; unsupported state halts replacement; bare-engine/wrapper boundary preserved; no serializer/clone abstraction | Tempfile deletion is conditional on actual native state parity; durable candidate files and serving loader remain |
| [F](F-cleanup-publication.md) | Four separately reviewable stages F0-F3; existing ownership inventory before new persistence; full preflight; one provider-qualified publication authority; logical snapshot distinguished from immutable bytes | F0 is immediate but not restart-safe; F1/F2 are separate changes; B does not depend on F3; unsafe rollback cannot discard fences |
| [G](G-optional-no-change-completion.md) | Go/defer gate based on observed unchanged frequency and validation work; frozen component/native snapshot; small fail-closed equality proof; additive outcome, no new status/equality/report hierarchy | Unknown comparisons validate normally; no-change has no candidate or new quality score; existing readers survive shortcut rollback |
| [README](README.md) | Reconciled sequence/dependencies, evidence labels, scope caps, contract-first fixtures, acceptance report and rollback obligations | No claims of a fresh clone, rerun probe or current HEAD verification in this document-only task |

## Requirement/test additions

Existing A/B/C/D/E/G requirement identities are retained where possible. A adds A-T7/A-T8 for gateway and request-accounting gates. C adds C-T8 for real consumer/extension need. D adds D-T8/D-T9 for optional telemetry and post-compile required-write failure. E adds E-T7 for copy/load failure and state-boundary behavior. G adds G0/G-T0 for the benefit decision. F is intentionally restaged with F0-T1 through F3-T2 and a mapping from original F1-F4 in README.

## Items deliberately not added

No new production module/package, database table definition, background reconciler, adapter registry, transaction engine, generic state serializer, custom SSE transport, mandatory OpenAPI extension, general equality framework or new terminal status family is supplied. A minimal durable F1 fact or optional consumer-backed C extension still requires the specified implementation evidence and review.

No new claims of provider performance, benchmark quality, resource cleanup confirmation or improved model accuracy are made. Code blocks remain implementation sketches. Existing immutable source links are retained as source-pack references, not silently replaced by a new branch baseline.

## Reading order

Start with [README](README.md), implement the selected slice's requirements and acceptance matrix, and use the common acceptance report at review. The [original review decisions](references/PRD-REVIEW-DECISIONS.md) explain the rationale but are not a checklist left unapplied. Original input files are preserved outside this updated archive.
