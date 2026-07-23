# ADR 0003: Gate DSPy Program Composition on Evaluation Evidence

## Status

Accepted

## Context

Fleet executes one fresh native `dspy.RLM` per Turn. Host preparation,
durability, authorization, and commit transitions are application concerns and
must not be hidden inside a DSPy execution Module. A custom `dspy.Module`
would add another model-program stage and operational surface.

## Decision

Fleet may introduce a custom DSPy Module only when all of the following are
documented and reviewed:

1. The workflow is a stable, repeatable multi-stage model program.
2. A representative evaluation dataset exists for the target workflow.
3. A measurable quality or efficiency metric is defined.
4. A native `dspy.RLM` baseline is recorded against that dataset and metric.
5. The Module does not own persistence, authorization, Artifact publication,
   Turn claims, or other durable backend transitions.

Until those conditions are met, the default remains one native `dspy.RLM` call
with host-mediated Tools and typed model-visible startup inputs.

## Consequences

- Signature typing and input validation remain an anti-corruption boundary,
  not a second execution layer.
- Optimizers and custom Modules require evidence rather than speculative
  abstraction.
- Backend lifecycle code remains independent of DSPy and its private protocols.
