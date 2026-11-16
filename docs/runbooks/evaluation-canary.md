# Evaluation canary

Check one Turn, saved trace, gold label, scoring and cleanup before a larger run.
Live models, Daytona, database access and judges require explicit authorization
and an agreed budget. This procedure does not approve a candidate.

## Prepare

1. Configure a dedicated server's runtime MLflow destination to the evaluation
   experiment/prefix. Keep the ordinary server separate.
2. Set agreed process-only model-call, token, retry and deadline limits. The
   default Turn timeout is `0` (no Fleet deadline).
3. Start `uv run fleet cli` with those overrides and a unique Session. Check
   `/config` without printing credentials, then select `/mode hybrid`.

## Execute once

Enter:

```text
Convert 72 degrees Fahrenheit to Celsius, rounded to one decimal place.
```

Expected answer: **22.2**; expected route: **flex**. Record Session, Turn and
trace IDs, answer, route and persistence confirmation. Check `/history` and
`/trace`, then `/exit`. Confirm the canary sandbox stopped.

## Label and score the saved trace

Confirm the trace is complete and belongs to the intended destination. Use the
fully qualified `trace:/...` reference when supplied by the client.

```bash
uv run fleet eval label TRACE_ID --answer '22.2 degrees Celsius' --value 22.2 --route flex
uv run fleet eval score TRACE_ID
```

Retain the scoring run ID, deterministic value result, available usage/cost,
elapsed time and cleanup state. A wrong answer is a result; a scorer error leaves
the check incomplete. Missing usage stays unknown.

## Automated collection and recovery

For generated cases, start a dedicated `fleet serve --port 8001` with the same
evaluation destination, then run:

```bash
uv run fleet eval run --url http://127.0.0.1:8001 --limit 1
```

Stop the temporary server and confirm cleanup afterward. If execution completed
but telemetry failed, recover history and score the saved trace. Never repeat a
Turn to repair readback, labels or scoring. A read timeout does not prove the
warehouse query stopped; inspect it before issuing another read.
