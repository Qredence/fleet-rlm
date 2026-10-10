# Fleet RLM live capability verification — 2026-10-10

## Scope and source identity

This bounded live probe used the Databricks model
`uscentral.ai_gateway.deepseek-v4-1-flash-service` for both Root and Sub-LM,
dedicated per-test SQLite databases, uniquely named disposable Daytona Volumes,
and the isolated MLflow experiment `fleet-rlm-live-capability` at the local
tracking server. Prompts and expected values were synthetic. No credential
values were written to this report.

The checkout was on `main` at `d733aca7d4de32c016bac29a9bcf96ad27b66181`
with tracked changes present. The canaries recorded that commit and dirty state,
but did not record a hash of the dirty diff. These runs are not clean-commit
certification. Cost was not available from the traces and is reported as
unknown.

## Results

| Case | Trace evidence | Outcome |
|---|---|---|
| Native DSPy and Daytona semantic queries | `tr-ed040b13542aa46850e327df18606e92`: 3 iterations, 36.7 s trace, 19,011 observed tokens. `llm_query` returned `ROOT`; `llm_query_batched` returned `ALPHA`, `BETA`, `GAMMA` in order; the verifier received the retained accumulator and returned `ok=true`. Typed submission and `database.commit` completed. `tr-1ec36a6c44d033cb57d049bec9280745`: targeted rerun, 3 iterations, 20.3 s RLM execution, 19,077 tokens, the same verified outputs and completed commit. | Behavior reconciles across MLflow spans and commit spans. The automated canary failed its legacy SSE event-name assertion (`finish` versus current `turn_finish`); the harness was updated after the one permitted targeted rerun, so its corrected end-to-end result was not rerun. Cleanup failures were followed by provider-side deletion of the uniquely named validation Volumes. |
| Two-child recursive batch | `tr-044e8563e2ef0e879269a384f1b9c0e1`: 4 Root iterations, 84.8 s, 39,027 tokens; two successful child spans returned `BATCH_TOKEN_ALPHA` and `BATCH_TOKEN_BETA`; peak child concurrency was 2; settlement and database commit completed. `tr-98ec8a1a443d5a07d35c6436723265f7`: one targeted rerun, 4 Root iterations, 145.3 s, 36,158 tokens, again with two completed children and peak concurrency 2. Child parentage and retained-root follow-up checks passed in both executions. | Both turns completed correctly, but the canary observed only 7 of 8 Daytona admission permits restored after closing the retained root. Its teardown also initially encountered a Volume still mounted by a sandbox. The test now drains `DaytonaRuntime.aclose`, tracks child sandbox ownership, and waits for provider deletion; these corrections could not be rerun within the one-rerun limit. This remains unresolved live evidence. |

Trace states were `OK`, but correctness above was established from span inputs and
outputs, child outcomes, and successful durable commit spans. Root/Sub-LM usage
is reported from the RLM execution spans; semantic-tool calls, batch prompts,
and child work are counted separately. Provider retries, cache-adjusted cost,
and monetary cost were not inferable from the available trace data.

## Cleanup and remaining gates

The isolated `fleet-rlm-live-mvp-*` and `fleet-rlm-recursive-batch-*` Daytona
Volumes and their attached sandboxes were enumerated by their unique test
prefixes, deleted, and confirmed absent. The local MLflow server was stopped;
full traces and logs remain only under the ignored `.scratch/live-capability/`
directory.

The remaining large-context, durable-memory, cancellation, and maintained-TUI
cases were not dispatched after the recursive lifecycle discrepancy. Deterministic
finalization and offline regression tests ran in `make check`. The live milestone
is incomplete until the retained-root admission result is explained and the
remaining bounded cases are run with their exact trace identities and cleanup
evidence.
