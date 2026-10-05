# Oolong benchmark adapter

> Historical runner guide. The Oolong runner, adapter, and scoring helpers have
> been retired. Commands below are preserved for historical context and are
> not supported operator instructions.

This archived page describes the former implementation as it existed before
the runner retirement. At the time, Fleet shipped a thin predict adapter for
the official [Oolong](https://github.com/abertsch72/oolong) long-context
aggregation benchmark. The adapter loaded Hugging Face rows from
`oolongbench/oolong-synth` or `oolongbench/oolong-real`, mapped them into the
locked Fleet native RLM invoke contract, and scored model answers with the
official helpers vendored from `src/eval/eval_helpers.py`.

The commands, source paths, and dataset pins below describe that former
implementation. They do not identify files or define a supported workflow in
the current repository.

## Former source details

- Benchmark repo: https://github.com/abertsch72/oolong
- HF datasets: `oolongbench/oolong-synth`, `oolongbench/oolong-real`
- Default HF revisions: synth `f0d59eaf0febf130664cfceb710436c8e3216b2b`; real `6bc9ef04866fcf005c9749b70649be69dd37fffb`
- Default HF builder configs: synth is single-config (`default`); real pins `dnd`
- Scoring at the time: `synth_process_response` / `dnd_process_response` from
  the official repo (then vendored under `scripts/benchmarks/oolong/scoring.py`)

## N=1 dry path (credential-free)

The former dry path used the bundled HF-shaped fixture, built locked Fleet
kwargs with an explicitly labeled request-concatenation shortcut, scored a
gold-aligned canned answer, and wrote a bounded receipt. It made no Daytona
sandbox or provider LLM calls.

```bash
uv run python scripts/benchmarks/run_oolong_predict.py \
  --output .scratch/benchmark-reports/oolong-dry-n1.json
```

What dry mode mocked:

- Daytona interpreter / sandbox admission
- Provider LLM inference (`--answer` or the default gold-aligned label string is scored directly)

What dry mode exercised:

- Fixture or Hugging Face row load (`--hf` for live dataset access; requires `datasets`)
- Locked kwargs build via `build_rlm_input_kwargs` + `build_session_context_manifest`
- Official Oolong scoring on the answer string

The former command to load one Hugging Face row instead of the fixture was:

```bash
uv run --with datasets python scripts/benchmarks/run_oolong_predict.py \
  --hf --split validation --index 0 \
  --output .scratch/benchmark-reports/oolong-dry-hf-n1.json
```

## Production/live path

The former live mode acquired an ephemeral volume-backed Daytona interpreter
through `fleet_rlm.daytona.runtime.acquire_ephemeral_interpreter` (shared
Daytona runtime path), stages `context_window_text` on the workspace
volume using `WorkspaceAttachmentPathPolicy` (same layout as Turn
`AttachmentContextCapsule` staging), constructs `build_native_rlm(...)`, and
invokes `await rlm.acall(interpreter_factory=interpreter_factory, **kwargs)` with `FleetJSONAdapter` so
wrap-up and parse re-asks match production Turns. Capsule `sandbox_path` and
`mount_root` are under the interpreter volume mount (typically
`/home/daytona/fleet`), not host-only temp paths.

That path required explicit operator authorization and configured provider
credentials.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_oolong_predict.py \
  --live --hf --split validation --index 0 \
  --output .scratch/benchmark-reports/oolong-live-n1.json
```

Optional MLflow 3.16 logging was available via `--mlflow-url` and
`--mlflow-experiment`; see the archived [Evaluation and Optimization guide](evaluation-optimization-runners.md)
for its former local tracking setup. Logging was skipped when
`--mlflow-url` was unset.

## Selecting a benchmark tier

Positional `--index` could not express a tier: in `oolong-synth` the rows were ordered by dataset
then context length, so the blog's 128K group sits at fixed-but-undocumented offsets. Select by
metadata instead:

```bash
# Oolong 128K tier (~50 trec_coarse rows at 131072 tokens)
uv run python scripts/benchmarks/run_oolong_predict.py --live --hf \
  --dataset synth --split validation \
  --context-len 131072 --row-dataset trec_coarse --limit 10 \
  --output .scratch/benchmark-reports/oolong-128k.json

# the 263K group
uv run python scripts/benchmarks/run_oolong_predict.py --live --hf \
  --dataset synth --split validation --context-len 262144 --limit 10 \
  --output .scratch/benchmark-reports/oolong-263k.json
```

Selection required `--hf`: the bundled fixture was a single fixed row. `oolong-real` had none of
these metadata columns, so tier selection was synth-only; the real split carried one
context size per row at `dnd`. The chosen criteria were recorded in the receipt under `selection`.

Rows were fetched by streaming with column/filter pushdown, so a run cost what it read (the
128K tier resolves in ~26MB) rather than materializing the whole split (~2GB for synth
validation, ~9GB for real).

## Context mapping

| HF field | Fleet mapping |
| --- | --- |
| `question` | `request` |
| `context_window_text` | production/live: UTF-8 attachment via `AttachmentContextCapsule` |
| `context_window_text` | dry only: concatenated into `request` when under the ~100k cap (**labeled dry shortcut; not production**) |

Model answers were read from typed `SUBMIT(answer=...)` as `str(result.answer)` in
live mode, then passed to the official scoring helpers.

## Receipt schema

Dry and live runs wrote `fleet.oolong-predict/v1` receipts through
`write_receipt_once`. Receipts record repository revision, dataset id/split,
mode (`dry`/`live`), context mode, row ids, and aggregate official scores. They
do not retain prompt bodies or provider payloads.

## Validation

```bash
uv run pytest tests/scripts/test_run_oolong_predict.py -q
uv run ruff check scripts/benchmarks/oolong scripts/benchmarks/run_oolong_predict.py tests/scripts/test_run_oolong_predict.py
```

Those checks proved adapter wiring only. They did not establish paper-comparable
Oolong campaign numbers or live certification.
