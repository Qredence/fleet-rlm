# Evaluation and Optimization

Fleet's routing and prompt optimization code lives in
`src/fleet_rlm/optimization/`; the maintained Turn path and its policy remain
the sources of truth. Benchmark, campaign, dataset, judge, and certification
runners and their archived guides have been retired; current supported behavior
is documented below.

## Runtime routing and optimization

Fleet's Root uses native DSPy semantic calls for prompt-only work and may use
the configured bounded child RLM path for tasks that need isolated iterative
Python execution. Routing policy is runtime behavior, not a model selection or
benchmark command. See the [DSPy and Daytona integration guide](dspy-integration.md)
for the current runtime boundary and the
[configuration reference](../reference/configuration.md) for the shipped
policy. Changes to routing or GEPA orchestration belong in
`src/fleet_rlm/optimization/` and its behavior-owning tests under
`tests/optimization/`.

GEPA orchestration remains in `optimization/gepa_runner.py`. Development
smoke runs use synthetic deterministic scoring, require explicit live
authorization for reflection calls, and always produce non-promotable
evidence. Production execution requires a validated block-all Daytona proof,
an explicit evaluator policy and capability binding, a trusted host metric,
and concrete train, selection, and held-out splits. Even a completed
production receipt sets `promotion_eligible` to false; it records candidate
and held-out evidence but does not change serving policy. The strict evaluator's
native RLM worker and cleanup ownership are described in the
[DSPy integration guide](dspy-integration.md).

## Keep optimization data partitions isolated

Optimization exports may connect examples through opaque Session, project, or
task-family provenance. The deterministic splitter keeps each connected group
in one partition and aims for 60/20/20 train, selection, and sealed-test shares.
Group isolation and at least five records per partition take priority; an
export that cannot satisfy them is rejected. Ungrouped exports use a seeded
record split. Manifests digest the validated content and sealed-test IDs while
omitting sealed-test content and group identity values. This prevents leakage
through declared provenance, but cannot establish independence when source
relationships were not recorded.

Offline tests establish deterministic orchestration and contract behavior.
They do not establish model quality, provider latency, or spend. Provider
behavior claims require the relevant bounded live contract described in the
[testing strategy](testing-strategy.md); the Daytona verifier does not certify
quality or authorize a policy change.

## MLflow runtime observability

MLflow is optional observation and feedback storage for Fleet runtime
execution. Its configured lifecycle exports bounded traces and TUI feedback;
it does not own execution, routing policy, or promotion decisions. Use the
[configuration reference](../reference/configuration.md) for tracking and
sanitization behavior, the [terminal UI guide](terminal-tui.md) for user
feedback, and [Trace V4 navigation](../reference/cli.md#recommended-trace-v4-view) for
the operator view. A trace shows the execution that occurred; it is not a
quality certification or a substitute for an explicitly designed evaluation.

Historical benchmark and campaign receipts retain their original scope and
revision. They do not describe current runner availability or current
production certification.
