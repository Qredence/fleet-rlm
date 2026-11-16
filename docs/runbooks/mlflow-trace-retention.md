# Manual MLflow trace retention

Fleet's selected replay snapshots and MLflow traces have separate retention
policies. Unlabelled replay snapshots are pruned at startup after 30 days; labelled
replays are retained. This runbook covers only Unity Catalog OTel trace tables. Run it manually
under a governed identity with `USE CATALOG`, `USE SCHEMA`, `SELECT`, and
`MODIFY` on the listed Fleet tables. Traces can remain longer than 30 days
between cleanup runs. Candidate artifacts and manifests have their own
retention policy and are not deleted here.

Before the first SQL warehouse preview, obtain the current warehouse rate and
estimate the preflight query cost with the workspace billing owner. Agree a
cap before running it. This runbook does not authorize a warehouse query or a
trace deletion by itself.

| Purpose | Catalog | Schema | Prefix |
|---|---|---|---|
| Runtime traces | `example_catalog` | `fleet_observability` | `fleet_rlm_runtime` |
| Evaluation traces | `example_catalog` | `fleet_observability` | `fleet_rlm_eval` |
| GEPA traces | `example_catalog` | `fleet_gepa` | `fleet_rlm_gepa` |

These values come from `config/fleet.toml.example`; the runtime prefix is also
`fleet_rlm_runtime` in the code fallback. If deployment overrides change these
values, use the effective configuration and verify each experiment's trace
location before cleanup. Each configured prefix can have `_otel_annotations`, `_otel_logs`,
`_otel_metrics`, and `_otel_spans` tables. Only operate on these three exact
Fleet prefixes. Do not use a schema-wide wildcard.

## Preflight and dry run

Verify effective locations and schemas, then record one UTC cutoff. Count rows
older than that cutoff for spans/logs/metrics. For annotations, preview the
linked-target predicate below as a `SELECT count(*)` before deleting. Confirm
actual `time`, `trace_id`, `span_id` and `target_id` columns and target semantics.
Stop on unexpected counts, missing columns or a partial preview. Keep reviewed
counts and exact table names with the cutoff.

## Governed deletion

After reviewing the dry-run counts, delete annotations first, then logs,
metrics, and spans. For each prefix, use the exact verified fully qualified
table names and the same recorded cutoff. The following are templates only;
substitute values only after the preflight checks above:

```sql
WITH expired_traces AS (
  SELECT trace_id
  FROM example_catalog.<verified_schema>.<exact_prefix>_otel_spans
  GROUP BY trace_id
  HAVING max(time) < TIMESTAMP '<recorded UTC cutoff>'
), expired_targets AS (
  SELECT trace_id AS target_id FROM expired_traces
  UNION
  SELECT spans.span_id AS target_id
  FROM example_catalog.<verified_schema>.<exact_prefix>_otel_spans AS spans
  JOIN expired_traces USING (trace_id)
)
DELETE FROM example_catalog.<verified_schema>.<exact_prefix>_otel_annotations
WHERE target_id IN (SELECT target_id FROM expired_targets);

DELETE FROM example_catalog.<verified_schema>.<exact_prefix>_otel_logs
WHERE time < TIMESTAMP '<recorded UTC cutoff>';

DELETE FROM example_catalog.<verified_schema>.<exact_prefix>_otel_metrics
WHERE time < TIMESTAMP '<recorded UTC cutoff>';

DELETE FROM example_catalog.<verified_schema>.<exact_prefix>_otel_spans
WHERE time < TIMESTAMP '<recorded UTC cutoff>';
```
