# Reading traces offline

The supervised client starts a local MLflow tracking server on
`127.0.0.1:5001` and stops it when the client exits, so traces outlive the
server only as rows in `.fleet_rlm/mlflow/mlflow.db`. Read that file directly
instead of pointing a client at the dead URI.

Resolve the path from the repository root; `tracking_uri` needs a `sqlite:///`
URI with a POSIX path.

```python
from pathlib import Path

from mlflow.tracking import MlflowClient

db = Path(".fleet_rlm/mlflow/mlflow.db").resolve()
client = MlflowClient(tracking_uri=f"sqlite:///{db.as_posix()}")

experiment = client.get_experiment_by_name("fleet-rlm")  # experiment_id "1"
traces = client.search_traces(
    experiment_ids=[experiment.experiment_id],
    order_by=["timestamp_ms DESC"],
    max_results=5,
)
trace = client.get_trace(traces[0].info.trace_id, display=False)
print(trace.info.status, len(trace.data.spans))
```

The experiment is `fleet-rlm`, id `1`. `search_traces` currently emits a
`FutureWarning` that `experiment_ids` is deprecated in favour of `locations`;
both accept the id string. `get_trace` prints the whole trace unless you pass
`display=False`.

## Expected span set for one Turn

A committed Turn carries, in rough order:
`fleet_turn`, `Turn.acquire_environment`, `Turn.prepare`,
`Turn.prepare_capabilities`, `Turn.stage_attachments`, `RLM.forward`,
`RLM.execute`, `RLM.root_lm`, `RLM.root_action`, `LM.__call__`,
`Predict.forward`, `FleetJSONAdapter.format`, `FleetJSONAdapter.parse`,
`Turn.cleanup`, `Turn.settlement`, `database.commit`.

A trace that stops at the preparation phases carries no trajectory to read;
compare it with the outcome the client reported and with
`.fleet_rlm/logs/latest.log` instead of interpreting the missing spans.

## Checks worth making

- **Status.** Compare `trace.info.status` with the outcome token the client
  showed, and with the failure diagnostic when they disagree.
- **Iterations against model calls.** Count `LM.__call__` spans and compare with
  the iteration count the client reported. One call per iteration is healthy; a
  large multiple means adapter retries or a retry storm, and `FleetJSONAdapter`
  span counts show how much of it was salvage rather than reasoning.
- **Settlement.** Check successful outcomes on `Turn.settlement` and
  `database.commit`, then reconcile them with the durable Run state. Span
  presence alone records an attempt and can also accompany a failed commit.
- **Cleanup.** `Turn.cleanup` present means the lifecycle reached teardown. It is
  evidence that cleanup ran, not that the provider confirmed deletion.

The client's `/trace` command prints the current Run's trace ID, which is the
shortest path from a live Turn to the `get_trace` call above.
