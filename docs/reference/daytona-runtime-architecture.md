# Daytona Runtime Architecture

This note records the current Daytona integration boundary for `fleet-rlm-dspy`.

## Official Daytona Baseline

The current implementation treats these Daytona docs as the normative baseline:

- Python SDK: [https://www.daytona.io/docs/en/python-sdk/](https://www.daytona.io/docs/en/python-sdk/)
- Volumes: [https://www.daytona.io/docs/en/volumes/](https://www.daytona.io/docs/en/volumes/)
- Recursive Language Models / DSPy: [https://www.daytona.io/docs/en/guides/recursive-language-models](https://www.daytona.io/docs/en/guides/recursive-language-models)

## What Is Directly Based On Daytona Docs

- Daytona clients are created through the official Python SDK entrypoints:
  - `Daytona()` when environment-based configuration is sufficient
  - `Daytona(DaytonaConfig(...))` when an explicit resolved config override is required
- Persistent Daytona storage is modeled as a real Daytona volume:
  - volume lookup/creation uses `client.volume.get(volume_name, create=True)`
  - sandboxes attach that volume through `CreateSandboxFromSnapshotParams(... volumes=[VolumeMount(...)])`
- Recursive Daytona work follows the guide's core invariants:
  - the root run executes through an iterative sandbox-backed code loop
  - `llm_query` is semantic-only and does not create child sandboxes
  - `rlm_query` and `rlm_query_batched` create true recursive child Daytona runs
  - each child run uses its own Daytona sandbox session and returns synthesized results to the parent

## Project-Specific Extensions

The repo intentionally extends Daytona's published guide shape with:

- a custom host-loop runner instead of a literal guide/demo implementation
- host callbacks for semantic and recursive subcalls
- prompt-handle storage and preview slicing
- automatic recursive decomposition between iterations
- richer websocket trace emission for the workspace transcript and canvas

These are intentional project behaviors, not alternative Daytona SDK semantics.

## Workspace Volume Contract

- The Daytona persistent volume name is derived from the authenticated workspace/tenant claim.
- `DAYTONA_TARGET` is used only as Daytona SDK routing/config input.
- `DAYTONA_TARGET` must not be treated as a workspace id, sandbox id, or volume name.
- The current internal Daytona volume mount path is `/home/daytona/memory`.
- Root and recursive child Daytona runs should share the same workspace-scoped persistent volume
  when one is configured, while still using distinct Daytona sandbox sessions per child run.
