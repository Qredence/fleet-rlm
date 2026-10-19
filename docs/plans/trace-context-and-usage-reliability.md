# Trace context and usage reliability

Trace `tr-42be00660f37c43f275f227c6ad00d39` returned a supported answer but
advertised an unavailable artifact to its nested RLM, retained an older artifact
without distinguishing its provenance, and reported inconsistent aggregate tokens.

## Scope clarification after worktree review

This plan describes the trace-context, artifact-provenance, and usage-reliability
workstream, not every change in the shared working tree. The scoring constraint
below applies to this workstream. Coexisting changes to stopping/verification
instructions and rejection-aware `fleet-metric-v6` scoring are separate work;
this review follow-up preserves them rather than treating their presence as
implementation of this plan or evidence of prior approval. The current scoring
policy is documented in [evaluation](../reference/evaluation.md). Existing scorer
and code identities continue to invalidate incompatible approval evidence.

Generation and decision usage derive missing totals only within an individual
receipt that reports both input and output counts. Generation receipts are
normalized before native DSPy rollup, so spans, stream events, and parent trackers
agree without duplicate accounting or totals invented from partial receipts.

## Implementation

1. Restrict native code-agent manifests to the files allowed by the existing
   child-copy policy. Preserve explicit selected-input delegation and isolation.
2. Add host-derived artifact changes to the persisted Turn and root trace:
   created, modified, retained, deleted, or unknown when evidence is incomplete.
   Keep the existing artifacts list as the Session inventory.
3. Isolate TypeSafe usage scopes, attach decision usage to its manual spans, and
   derive missing totals only from reported input and output counts. Roll these
   entries into native DSPy tracking once, including failed calls.
4. Remind generated programs to import dependencies and keep submission short.
   Keep model-dependent behavior separate from enforced runtime guarantees.
5. Add regressions and run focused tests, then the complete non-live gate.

## Acceptance

- A nested RLM does not see root-only artifact paths in its manifest; explicitly
  selected artifact transfer still works.
- An unchanged input artifact is identified as retained, not newly produced;
  missing evidence does not imply deletion.
- Decision and generation span usage agree with native rollups, with unknown
  counts preserved and no duplicate aggregation.
- Recovered Python failures, deadlines, budgets, and success-only promotion keep
  their existing contracts. Scoring/approval semantics and dependencies stay fixed.
- No live Daytona/model/evaluation runs, commits, or pushes are part of this work.
