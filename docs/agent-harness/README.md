# Agent Harness

This directory defines the repo-local engineering loop for the canonical
RLM-native backend.

## Reading Path

1. `AGENTS.md`
2. [Feedback loop](feedback-loop.md)
3. [Architecture invariants](architecture-invariants.md)
4. [Drift control](drift-control.md)
5. [Quality standard](quality-score.md)
6. [Codebase map](../reference/codebase-map.md)
7. `src/fleet_rlm/AGENTS.md`

## Harness Contract

- `src/fleet_rlm/` is the sole Python backend package.
- `openapi.yaml` is the backend HTTP contract, and
  `tools/fleet-tui/src/generated/openapi.ts` is its generated client type surface;
  `make api-sync` owns both.
- Alembic owns production schema creation.
- retained scripts are inventoried in `scripts/README.md` and are help-safe.
- the normal local gate is `make check`.
- live Daytona/DSPy checks are explicit and use only canonical `FLEET_*`
  configuration.

Frontend adaptation is a separate effort and is not part of this backend gate.
