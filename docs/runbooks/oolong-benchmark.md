# OOLONG-Synth task benchmark

Use the [task benchmark workflow](benchmark-tasks.md) and
[OOLONG notebook](../../notebooks/oolong_benchmark.ipynb).
The notebook loads a pinned HF revision and scores saved Fleet answers with the official
synth scorer, alongside the optional Fleet judge suite in MLflow.
The former database comparison profiles and dedicated benchmark service setup are retired.
Historical inputs, receipts, and traces are preserved.
