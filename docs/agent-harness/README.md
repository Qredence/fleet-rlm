# Agent Harness

This directory is the repo-local harness engineering hub for `fleet-rlm`. It adopts the strict
model from OpenAI's harness engineering guidance: short root instructions, durable docs as the
system of record, a complete local feedback loop, and mechanical drift checks.

The current retrofit baseline is strong. The harness audit scored the repo at `95/100`; this work
keeps the existing structure and tightens the remaining control surfaces rather than replacing the
project with a scaffold.

## Agent Reading Path

Start here when a task spans more than one file:

1. `AGENTS.md` - root table of contents and command map.
2. `docs/agent-harness/feedback-loop.md` - local Codex loop for bootstrap, app smoke, validation,
   trace capture, and final reporting.
3. `docs/agent-harness/architecture-invariants.md` - boundaries that should not drift.
4. `docs/agent-harness/drift-control.md` - checks that enforce the docs and generated contracts.
5. `docs/reference/codebase-map.md` - source layout and owners.
6. `src/fleet_rlm/AGENTS.md` or `src/frontend/AGENTS.md` for subsystem work.

## Harness Contract

- The root `AGENTS.md` stays under the agreed line budget and links to durable docs.
- `.codex/` is the local Codex surface for actions, hooks, environment bootstrap, and subagent
  roles.
- Generated files are only changed by sync/build commands.
- Script inventory is authoritative: retained top-level Python helper scripts are listed in
  `scripts/README.md` and support `--help`.
- Architecture boundaries are checked with targeted structural rules before broad manual review.

## Local First

The first complete feedback loop is local Codex, not CI. A useful local run proves:

- dependencies can bootstrap,
- the API/UI can be started or a running instance can be inspected,
- at least one Workbench path and one secondary surface can be smoke-tested,
- MLflow/runtime identifiers are captured when available,
- the smallest relevant validation lane passes,
- the final report names commands, outputs, and any unverified live surfaces.

Use `docs/agent-harness/feedback-loop.md` for the exact lane.
