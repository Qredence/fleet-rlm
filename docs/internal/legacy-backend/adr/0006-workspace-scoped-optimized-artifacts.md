# ADR-0006: Offline GEPA Selection and Workspace-Scoped Activation

- **Status:** Accepted (Phase 8)
- **Date:** 2026-07-11
- **Supersedes:** None
- **Superseded by:** None

## Context

DSPy GEPA uses its validation set to guide Pareto search and select a winner.
Calling that set a holdout conflates optimizer selection with independent
promotion evidence. Optimized instructions also cannot safely become a global
code default because tenants and workspaces own distinct approved datasets,
model profiles, and operational constraints.

## Decision

Fleet runs GEPA only in the offline quality lane. Training drives reflection,
selection is GEPA's `valset`, and a sealed promotion-test partition is evaluated
once on baseline and winner after compilation. GEPA produces a candidate, never
an activation decision.

Approved artifacts are immutable versions. A separate activation pointer is
keyed by tenant, workspace, Managed Target kind, and Managed Target ID. Atomic
activation retains the preceding version for immediate rollback. Database
migrations and promotion approval never create or change the pointer. If quality
activation is disabled or no pointer exists, the owning module factory or Skill
catalog returns its normal default without side effects.

## Consequences

- Selection scores may diagnose search but cannot authorize promotion.
- Activation is explicit, authorized, reversible, and isolated by workspace.
- Module deployment artifacts are state-only JSON and Skill artifacts are
  Markdown; checkpoints and evidence are never executable deployment formats.
- Existing runs/datasets/drafts remain readable but fail closed for promotion.
- Global code and catalog defaults remain stable throughout Phase 8 migration.
