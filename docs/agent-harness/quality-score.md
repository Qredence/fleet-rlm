# Harness Quality Score

The current repo harness baseline is `95/100` from the local harness audit. Treat that score as a
snapshot: the real quality bar is whether agents can find the right instructions, run the right
loop, and detect drift before CI.

## Current Grades

| Domain | Grade | Notes |
| --- | --- | --- |
| Agent instructions | Strong | Root `AGENTS.md` is now a compact map; subsystem guides remain deeper sources. |
| Repo map | Strong | `docs/reference/codebase-map.md` and this hub provide source layout and reading order. |
| Validation lane | Strong | `Makefile`, `.codex` actions, and docs expose focused checks. |
| CI and release hygiene | Strong | Release, docs, frontend, and security checks are wired through `make`. |
| Observability | Strong | MLflow workflows, runtime status, and optional live traces are documented. |
| Drift control | Improving | Harness checks now cover root budget, docs reachability, `.codex`, scripts, and boundaries. |

## Current Cleanup Targets

- Keep root `AGENTS.md` below the agreed line budget as the repo evolves.
- Keep `.codex/environments/environment.toml` action names aligned with `Makefile` targets.
- Expand structural checks only when a real drift pattern appears; avoid broad lint duplication.
- Refresh this score after large architecture, release, or Codex-environment changes.

## Quality Standard

A change is harness-complete when:

- the agent reading path still points to current files,
- the local Codex feedback loop still runs without live credentials in safe mode,
- generated artifacts have explicit sync/check commands,
- top-level scripts are inventoried and help-safe,
- final reports name the validation lane and any skipped live evidence.
