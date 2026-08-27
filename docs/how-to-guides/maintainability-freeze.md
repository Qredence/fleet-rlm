# Maintainability freeze

P34 is the certification gate for the P26–P33 maintainability hardening. It
freezes implementation ownership and verifies that the structural changes did
not change Fleet's public behavior, persistence model, Workspace Memory format,
provider lifecycle, or native DSPy execution contract.

## Ownership matrix

| Concern | Canonical owner | Regression proof |
| --- | --- | --- |
| Turn stream, terminal ordering, and final cleanup | `chat/turn_coordinator.py` | coordinator and stream contract suites |
| Result validation, Artifact publication, and atomic Turn commit | `chat/run_lifecycle.py` | result snapshot and lifecycle contract suites |
| Native RLM execution and worker observation | `rlm/runtime.py`, `rlm/events.py` | native RLM and recursive delegation suites |
| Waits for already-started asynchronous effects | `runtime/owned_effect.py` | owned-effect and cancellation suites |
| Recursive child acquisition, settlement, and late ownership | `daytona/recursive_child_runtime.py` | child cleanup, cancellation, and provider-boundary suites |
| Installed and fallback Workspace agent protocol | `daytona/workspace_agent/`, `daytona/workspace_agent/runtime.py` | Workspace agent handshake and file/Memory suites |
| Workspace Memory fail-soft classification | `daytona/memory_diagnostics.py` | Memory diagnostics and observability sanitation suites |
| Configuration field metadata and editor inventory | `config.py`, `config_policy.py` | configuration policy and settings API suites |
| Canonical live/durable TUI projection | `tools/fleet-tui/src/tui/canonical.ts`, `live-adapter.ts`, `durable-adapter.ts`, `turn-reducer.ts` | `turn-reducer-invariants.test.ts` |

Each row has one transformation owner at its seam. Adapters may translate a
wire representation into the canonical representation, but downstream code
must not repeat that conversion. Private modules may remain separate when they
keep ownership local; file-count reduction is not a freeze objective.

## Freeze constraints

The P34 baseline does not include:

- model routing, LM selection, recursion width/depth, latency, throughput,
  batching, or Sandbox pooling changes;
- public FastAPI, OpenAPI, SSE, or generated-client changes;
- SQLite schema/state-machine or Workspace Memory format changes;
- new DSPy private-API dependencies or weakened provider cleanup guarantees;
- deletion of a compatibility path that still has a supported consumer.

Superseded private helpers may be deleted only after the deterministic matrix
is green and their consumers have been checked. Legacy Memory migration,
wire-format adapters, test/live monkeypatch seams, and explicit process-only
Workspace-agent compatibility remain supported behavior where their owning
tests require them.

## Certification matrix

Run the deterministic gates from the exact delivery tip and retain the command
and pass/fail result in the delivery record:

```bash
make check
make check-security
make build-release
make check-release
git diff --check
```

`make check` covers the non-live Python, contract, generated-artifact, TUI,
codebase-boundary, and documentation lanes. The credentialed Daytona lane is
explicit and must be run separately with `FLEET_LIVE=1` using the maintained
live-proof scripts/tests. Record only the commit SHA, safe test/profile or
snapshot identifiers, command, and pass/fail summary; never retain credentials,
provider tokens, or Workspace Memory contents.

The live gate must cover native root execution, recursive child acquisition and
confirmed cleanup, shared-Volume Workspace operations, the installed agent
handshake, and absence of provider resources after terminal Run paths. A live
proof that was not run remains unverified; deterministic green is not a
substitute for that evidence.

## Drift response

When a guardrail fails, repair the owning P26–P33 seam or update this guide and
its matching contract check in the same change. Do not hide a failed gate by
loosening a public assertion, adding a second transformation pipeline, or
reintroducing a deleted alias. Update [the codebase map](../reference/codebase-map.md)
and [architecture invariants](../agent-harness/architecture-invariants.md) when
module ownership changes.
