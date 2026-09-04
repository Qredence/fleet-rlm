# Runtime benchmark baseline

Phase 0 fixes the receipt schema at `fleet.runtime-benchmark/v1`. A receipt is
one or more aggregate scenario measurements plus the exact Fleet version,
commit, config digest, SnapshotDefinition name, and Root/Sub model IDs. It
must never retain provider prompts, responses, private reasoning, credentials,
private paths, or raw provider errors.

The fixed corpus is [`tests/fixtures/runtime-benchmark/corpus-v1.json`](../../tests/fixtures/runtime-benchmark/corpus-v1.json): exact calculation,
long-document evidence, repository analysis, tabular analysis, multi-source
comparison, artifact creation, cancellation during code, recursive-child
timeout, and child-cleanup failure.

The runtime executor writes one aggregate result per scenario. Capture a
validated baseline receipt for the current `0.7.6` checkout with:

```bash
make benchmark-runtime-baseline \
  RUNTIME_BENCHMARK_RESULTS=.scratch/runtime-benchmark-results.json \
  RUNTIME_BENCHMARK_OUTPUT=.scratch/runtime-benchmark-baseline.json
```

`RUNTIME_BENCHMARK_RESULTS` is an array of `RuntimeBenchmarkResult` objects.
The capture command supplies selected-policy provenance itself and rejects
unknown fields, so prompt-like content cannot enter a receipt. It does not run
provider work; operator-controlled live scenario execution remains explicit.

For Daytona startup-only measurements, use `make benchmark-daytona-lifecycle`.
It records Volume readiness, Sandbox creation, mount verification, broker
startup, first execution, shutdown, and deletion separately. Runtime receipts
add sandbox lookup/start, child deletion confirmation, and code-execution
phases where those phases apply.

Public behavior is frozen independently in
[`tests/fixtures/runtime-events/`](../../tests/fixtures/runtime-events/):
successful, failed, cancelled, and recursive Run sequences. Any intentional
public event change requires a versioned contract change rather than editing a
baseline silently.
