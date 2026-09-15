# Oolong benchmark adapter

Fleet ships a thin predict adapter for the official [Oolong](https://github.com/abertsch72/oolong)
long-context aggregation benchmark. The adapter loads Hugging Face rows from
`oolongbench/oolong-synth` or `oolongbench/oolong-real`, maps them into the
locked Fleet native RLM invoke contract, and scores model answers with the
official helpers vendored from `src/eval/eval_helpers.py`.

Historical Fleet v0.6.x Oolong scripts (`scripts/oolong_official_eval.py`,
`scripts/benchmarks/oolong.py`, and Fleet `_synth_score`) are **not** the source
of truth and are intentionally absent on current tip.

## Source of truth

- Benchmark repo: https://github.com/abertsch72/oolong
- HF datasets: `oolongbench/oolong-synth`, `oolongbench/oolong-real`
- Scoring: `synth_process_response` / `dnd_process_response` from the official
  repo (vendored under `scripts/benchmarks/oolong/scoring.py`)

## N=1 dry path (credential-free)

The default dry path uses the bundled HF-shaped fixture, builds locked Fleet
kwargs with an explicitly labeled request-concatenation shortcut, scores a
gold-aligned canned answer, and writes a bounded receipt. No Daytona sandbox and
no provider LLM calls are made.

```bash
uv run python scripts/benchmarks/run_oolong_predict.py \
  --output .scratch/benchmark-reports/oolong-dry-n1.json
```

What is mocked in dry mode:

- Daytona interpreter / sandbox admission
- Provider LLM inference (`--answer` or the default gold-aligned label string is scored directly)

What is exercised in dry mode:

- Fixture or Hugging Face row load (`--hf` for live dataset access; requires `datasets`)
- Locked kwargs build via `build_rlm_input_kwargs` + `build_session_context_manifest`
- Official Oolong scoring on the answer string

To load one Hugging Face row instead of the fixture:

```bash
uv run --with datasets python scripts/benchmarks/run_oolong_predict.py \
  --hf --split validation --index 0 \
  --output .scratch/benchmark-reports/oolong-dry-hf-n1.json
```

## Production/live path

Live mode acquires an ephemeral volume-backed Daytona interpreter through
``fleet_rlm.daytona.provisioning.acquire_ephemeral_interpreter`` (shared
``SandboxProvisioner`` seam), stages `context_window_text` on the workspace
volume using `WorkspaceAttachmentPathPolicy` (same layout as Turn
`AttachmentContextCapsule` staging), constructs `build_native_rlm(...)`, and
invokes `await rlm.acall(interpreter, **kwargs)` with `FleetJSONAdapter` so
wrap-up and parse re-asks match production Turns. Capsule `sandbox_path` and
`mount_root` are under the interpreter volume mount (typically
`/home/daytona/fleet`), not host-only temp paths.

This path requires explicit operator authorization and configured provider
credentials.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_oolong_predict.py \
  --live --hf --split validation --index 0 \
  --output .scratch/benchmark-reports/oolong-live-n1.json
```

Optional MLflow 3.16 logging is available via ``--mlflow-url`` and
``--mlflow-experiment``; see `Evaluation and monitoring
<evaluation-optimization.md>`_ for local tracking setup. Logging is skipped when
``--mlflow-url`` is unset.

## Context mapping

| HF field | Fleet mapping |
| --- | --- |
| `question` | `request` |
| `context_window_text` | production/live: UTF-8 attachment via `AttachmentContextCapsule` |
| `context_window_text` | dry only: concatenated into `request` when under the ~100k cap (**labeled dry shortcut; not production**) |

Model answers are read from typed `SUBMIT(answer=...)` as `str(result.answer)` in
live mode, then passed to the official scoring helpers.

## Receipt schema

Dry and live runs write `fleet.oolong-predict/v1` receipts through
`write_receipt_once`. Receipts record repository revision, dataset id/split,
mode (`dry`/`live`), context mode, row ids, and aggregate official scores. They
do not retain prompt bodies or provider payloads.

## Validation

```bash
uv run pytest tests/unit/scripts/test_run_oolong_predict.py -q
uv run ruff check scripts/benchmarks/oolong scripts/benchmarks/run_oolong_predict.py tests/unit/scripts/test_run_oolong_predict.py
```

These checks prove adapter wiring only. They do not establish paper-comparable
Oolong campaign numbers or live certification.
