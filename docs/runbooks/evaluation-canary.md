# Evaluation canary

This procedure checks evaluation tracing, expectations, scoring, and cleanup
with one deterministic Turn. Live runs require authorization and available
budget. P5.5 retains its original $60 cumulative cap, $48 forecast target, and
$12 reserve; this is not a new allowance. Reconcile available prior Gateway,
Daytona, and warehouse usage before running. Record reporting delays and use a
conservative forecast when dedicated model rates are unavailable.

## Prepare an isolated server

Check that loopback port 8001 is unused. Do not reuse or terminate an unrelated
server. Resolve the configured evaluation experiment and UC prefix from
`FleetSettings(mlflow_mode="databricks").mlflow_target().eval_experiment_name` and
`FleetSettings.mlflow_eval_trace_table_prefix`; use those values for the two
overrides below. The names shown are the checked-in defaults. Keep the
configured catalog, schema, warehouse, and model routes. Do not edit `.env`.

If `.env` contains duplicate keys, uv's environment-file loader may select an
earlier entry than FleetSettings/python-dotenv. The current local file has an
obsolete SQLite DSN followed by the intended PostgreSQL DSN. Preserve the file
and use this wrapper for the commands below, replacing
`uv run --env-file .env fleet` with `fleet_env`:

```bash
fleet_env() {
  uv run python -c 'import os, sys; from dotenv import load_dotenv; load_dotenv(".env", override=False); os.execvp("uv", ["uv", "run", "fleet", *sys.argv[1:]])' "$@"
}
```

It resolves duplicate entries with python-dotenv, preserves existing process
overrides, and executes `uv run fleet ...` with credentials in memory. For the
recovery Python snippet below, run it from the same environment by loading
`.env` with `load_dotenv(".env", override=False)` before importing MLflow.

```bash
FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME=/Shared/fleet-rlm-eval \
FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX=fleet_rlm_eval \
FLEET_PORT=8001 \
uv run --env-file .env fleet cli databricks --session evaluation-UNIQUE
```

Use a unique Session name. Run this in a real terminal/PTY. The terminal
launcher owns the temporary API server and stops it on `/exit`. For the bounded
P5.5 canary, apply the previously agreed process-only limits: 20 Turn model
calls, zero automatic model retries, 2048-token root/sub outputs, and a
1024-token reflection output. Keep the 900-second Turn deadline. Verify `/config`
before entering the task; do not print secret values.

Use `/mode hybrid`, then enter exactly:

```text
Convert 72 degrees Fahrenheit to Celsius, rounded to one decimal place.
```

The expected value is **22.2** and the current computation rubric expects **Flex**.
The historical run with a gold direct label remains recorded as a mismatch;
new label versions do not erase that evidence. Retain the answer, reported route, trace ID,
Session ID, persistence confirmation, and `/history` and `/trace` output. Exit
with `/exit` to flush tracing and close the temporary runtime. Confirm the
canary sandbox reaches the stopped state through Daytona; do not delete
unrelated resources or change shared warehouse settings.

## Verify and label the saved trace

Use the fully qualified `trace:/catalog.schema.prefix/id` reference printed by
the client. Confirm the trace belongs to the configured evaluation experiment
and exact UC prefix, is complete, and has spans. Allow one 180-second readback
window for a cold warehouse; poll the same trace and record any timeout.

```bash
uv run --env-file .env fleet trace verify TRACE_ID --scope evaluation --mlflow databricks
uv run --env-file .env fleet eval label TRACE_ID --mlflow databricks \
  --status synthetic --group synthetic-direct-00 \
  --answer 22.2 --value 22.2 --route flex --critical
```

For a bounded read and scoring of the already executed Turn, use the following
Python API from `uv run --env-file .env python`. Replace `TRACE_ID` first:

```python
import os
import mlflow
from fleet_rlm.config import get_settings
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimize.evaluation import read_evaluation_trace, score_evaluation_traces

settings = get_settings("databricks")
settings.prepare_mlflow_auth()
target = settings.mlflow_target()
experiment_id = configure_mlflow_observability(
    target.eval_experiment_name,
    target.tracking_uri,
    trace_catalog=settings.mlflow_trace_catalog,
    trace_schema=settings.mlflow_trace_schema,
    trace_table_prefix=settings.mlflow_eval_trace_table_prefix,
    tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
)
for name, value in settings.judge_environment().items():
    os.environ[name] = value
trace = read_evaluation_trace(
    "TRACE_ID",
    experiment_id=experiment_id,
    uc_location=(
        settings.mlflow_trace_catalog,
        settings.mlflow_trace_schema,
        settings.mlflow_eval_trace_table_prefix,
    ),
)
result = score_evaluation_traces(
    [trace],
    judge_model=settings.fleet_judge_model,
    judge_lm=settings.build_judge_lm(),
    jev_lm=settings.build_jev_lm(),
)
print(result.run_id, result.metrics)
mlflow.flush_trace_async_logging()
```

Read expectations and feedback back from the saved trace. Record the scoring
run ID, deterministic value check, available token/cost fields, elapsed time,
and provider cleanup state. A scorer error is incomplete evaluation; a negative
score is a measured result and must remain visible. A direct numeric task can
be inapplicable to retrieval scoring.

When reading feedback after a scoring retry, filter assessment metadata
`mlflow.assessment.sourceRunId` to the new scoring run ID. Historical failed
assessments remain as audit history. For the configured Gateway judge, assign
both values returned by `judge_environment()` as above; using `setdefault`
can leave an unrelated provider URL paired with the Databricks token.

After a diagnostic scorer update, use `score_evaluation_traces([trace], ...)` on
the saved trace to create a new scoring run without repeating the Turn. Confirm
the returned run ID and `fleet.trace_scorer_version` on each new diagnostic
assessment, then read back only assessments whose
`mlflow.assessment.sourceRunId` matches that run. Older assessments remain
unchanged and visible for audit.

## Automated collection

For `fleet eval run`, start a dedicated `fleet serve --port 8001` in one
terminal using the same experiment/prefix overrides and `--env-file .env`.
In a second terminal:

```bash
uv run --env-file .env fleet eval run --url http://127.0.0.1:8001 --limit 1
```

The command verifies the destination and readable labels before scoring. Its
unique Session namespace prevents reuse of a previous invocation's idempotent
result. Stop the temporary server and confirm sandbox cleanup afterward.

## Recover without repeating execution

If execution completed but readback, assessment writes, or scoring failed,
preserve the trace and Session IDs. Repair the failed stage and rerun only the
label/readback/scoring operation on that trace. Check durable Session history
for transport failures before considering any new task. A read timeout does
not establish whether the SDK's SQL statement has finished; inspect its status
before starting another read. Never rerun a Turn merely to recover telemetry.

Update the audit plan with receipts and cumulative estimated/observed spend.
Keep P5.5 open for reviewed examples, a GEPA run, and final cost reconciliation.
This canary does not approve or install a candidate.
