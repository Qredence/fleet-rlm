# Phase 9 direct-RLM promotion matrix

**Status:** Pending live local evidence (2026-07-10)

`legacy_agent_runtime` remains the default. `direct_rlm` is promotion-gated and
currently opt-in through server configuration only; this document is not
approval to change `AppConfig.execution_backend`.

## Required local matrix

Run `scripts/validate_rlm_e2e_trace.py --promotion-gate` against two distinct
local API servers: one configured with `EXECUTION_BACKEND=legacy_agent_runtime`
and one with `EXECUTION_BACKEND=direct_rlm`. The harness runs three turns per
backend and writes per-run duration, token, fallback, terminal-error, and
median-comparison evidence under `output/phase-04/qre-301/promotion-*/`.

The direct-RLM side must prove all of the following in the captured evidence:

- selected trusted `long-context` Skill;
- uploaded synthetic sentinel attachment passed only by attachment ID;
- a resumed session across all three turns;
- a session-scoped Markdown artifact created and read back through the
  artifact tool, including the expected content marker and SHA-256 checksum;
- WebSocket and execution event streams, session trace-debug response, and a
  trace verified against an explicitly enabled MLflow tracking server.

The matrix fails on a terminal error, fallback/degraded path, absent token
evidence, or a direct median duration/token regression above the configured
threshold. Passing this harness is a promotion prerequisite, not an automatic
configuration mutation.

## Current evidence

No live matrix evidence is recorded in this repository. Capture it only in an
environment with `DAYTONA_API_KEY`, `DSPY_LM_MODEL`, `DSPY_LM_API_KEY`,
`MLFLOW_TRACKING_URI`, `MLFLOW_ENABLED=true`, a database URL, authenticated
runtime setup, and both local API servers available.
