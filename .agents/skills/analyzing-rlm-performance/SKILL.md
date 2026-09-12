---
name: analyzing-rlm-performance
description: Investigate measured Fleet RLM latency, cost, or quality regressions.
metadata:
  compatibility: Fleet-RLM repository with its pinned DSPy and Daytona dependencies.
---

# Analyzing RLM performance

Identify the regressed outcome and the effective `config/fleet.toml` policy.
Use comparable receipts or a deterministic harness to locate the bottleneck;
separate model time, interpreter/broker work, child execution, and cleanup.
A lower iteration count or shorter prompt is useful only if semantic quality survives.

Read only the reference matching the measured bottleneck:

- [RLM loop](references/rlm-loop.md): model calls, prompt volume, retries, and answer quality.
- [Broker](references/broker.md): callback polling, dispatch, output, and cell latency.
- [Snapshots](references/snapshots.md): sandbox startup and resource reuse.

Use the repository [architecture](../../../ARCHITECTURE.md) for ownership changes
and [validation matrix](../../../AGENTS.md#validation-selection) for affected checks.
Compare the same workload, policy, dependency versions, and measurement boundaries
before and after a bounded change; identify intentional differences explicitly.
Report baseline, changed dimensions, semantic outcomes, and whether evidence is
local, historical, or from an explicitly authorized live run.
