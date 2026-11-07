# Archive

Frozen historical documents. Nothing here is maintained, and none of it describes
current behavior. Read [the documentation index](../index.md) for what Fleet does today.

## Why these moved out of the live tree

| Document | Reason |
| --- | --- |
| [fleet-rlm-prds/](fleet-rlm-prds/README.md) | The A–G implementation instructions and their acceptance reports. PRDs A–E shipped; C, F and G were implemented by `#653` and `#657` while the documents still said "proposed". |
| [longcot-direct-benchmark.md](longcot-direct-benchmark.md) | Retired experimental workflow. Its own header says the comparator and dataset-specific policy are unsupported. |
| [longcot-optimization.md](longcot-optimization.md) | Retired, same policy. |

## Conventions

- These files keep their original text, including stale claims and baseline SHAs. They
  are evidence of what was decided at the time, not a description of the current runtime.
- `scripts/check_docs.py` skips this directory, so a link frozen against an old layout
  cannot fail the documentation gate.
- Live documentation lives in [docs/reference](../reference/index.md) and
  [docs/runbooks](../runbooks/). Measured benchmark reports stay local and untracked.
