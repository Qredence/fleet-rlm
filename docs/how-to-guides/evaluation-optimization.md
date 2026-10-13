# Evaluation and Optimization

Fleet's routing and prompt optimization code lives in
`src/fleet_rlm/optimization/`; the maintained Turn path and its policy remain
the sources of truth. Benchmark, campaign, dataset, judge, and certification
runners and their archived guides have been retired; the isolated Skill-selection
study below is an opt-in experiment. Current supported behavior
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

## Isolated Skill-selection comparison

`uv run python scripts/evaluate_skill_selection.py --plan` validates the 36-case
public synthetic fixture without reading credentials or calling providers.
Eighteen task families calibrate decisions and eighteen separate families are
held out. Fixtures are reviewed examples, not observed user traces or proof of
production quality.

With explicit operator authorization, install the optional evaluation dependencies
with `uv sync --extra optimize --dev`, ensure Deno is on PATH, and run:

```bash
uv run python scripts/evaluate_skill_selection.py --run
```

The script reads the existing host `TYPESAFE_API_KEY` and configured Root model
credentials. DSPy's certified 3.4.0 TypeSafe extra supplies the SDK. No secrets
enter the isolated interpreter. `--jev-model` defaults to `jev-latest`; record the
model alias and execution date when interpreting results. Experimental decision
APIs can change; do not upgrade DSPy outside its certified workflow.

One top-level Noul decision per Skill allows zero or multiple recommendations.
ReAnchor fits thresholds using calibration examples only. The comparison freezes
those thresholds and disables inference caches for held-out measurements.
Both arms use the same native RLM builder and JSON adapter, capped at four
selection iterations, with alternating arm order. The default DSPy
PythonInterpreter provides an isolated selection environment: it loads guidance
only and does not browse, execute resource scripts, or carry out fixture tasks.

Baseline Root discovery is compared with an optional `recommend_skills` tool.
The host filters already-loaded and forbidden candidates, caps recommendations
to remaining loading slots, and breaks probability ties in catalog order. Root
still decides whether to call `load_skill`. Explicit selections bypass prediction;
failed advice returns a safe unavailable result and leaves the normal loader usable.

Reports are written to a new path under `.fleet_rlm/evaluation/skill-selection/`.
They include fixture identity, catalog versions, frozen thresholds, per-case
selection results, failures, model invocation counts, reported usage, and latency.
Missing provider costs are never estimated. Counts are managed DSPy invocations,
not a claim about SDK retries or billed HTTP requests. Reports never overwrite
existing evidence.

The summary recommends retaining Root selection or considering a separately
authorized end-to-end trial. An improvement in this small selection-only dataset
does not establish faster or better complete Turns and never promotes a runtime
policy. This experiment adds no production selector, endpoint, or setting.
