# Manual MLflow trace retention

Fleet's selected replay snapshots and MLflow traces have separate retention
policies. Selected replay snapshots are pruned by Fleet at startup after 30
days. This runbook covers only Unity Catalog OTel trace tables. Run it manually
under a governed identity with `USE CATALOG`, `USE SCHEMA`, `SELECT`, and
`MODIFY` on the listed Fleet tables. Traces can remain longer than 30 days
between cleanup runs. Candidate artifacts and manifests have their own
retention policy and are not deleted here.

Before the first SQL warehouse preview, obtain the current warehouse rate and
estimate the preflight query cost with the workspace billing owner. Agree a
cap before running it. This runbook does not authorize a warehouse query or a
trace deletion by itself.

The checked-in Databricks destinations are:

| Purpose | Catalog | Schema | Prefix |
|---|---|---|---|
| Runtime traces | `uscentral` | `fleet_observability` | `fleet_rlm_runtime` |
| Evaluation traces | `uscentral` | `fleet_observability` | `fleet_rlm_eval` |
| GEPA traces | `uscentral` | `fleet_gepa` | `fleet_rlm_gepa` |

These values come from `config/fleet.toml`; if deployment overrides change
them, use the effective configuration and verify each experiment's trace
location before cleanup. Genie metadata discovery also found shared/base and
legacy Fleet objects in these schemas. They are outside this cleanup scope.
Each configured prefix can have `_otel_annotations`, `_otel_logs`,
`_otel_metrics`, and `_otel_spans` tables. Only operate on these three exact
Fleet prefixes. Do not use a schema-wide wildcard.

## Preflight and dry run

1. Confirm the active Unity Catalog catalog, schema, exact four table names for
   each prefix, and the bound MLflow experiment trace locations. If a table is
   absent, skip it and record that fact; do not create it for cleanup.
2. Run `DESCRIBE TABLE EXTENDED` for each existing table. Confirm its schema and
   timestamp type before using a time predicate. Databricks documents the `time`
   column on spans and logs; verify the current metrics and annotations schemas
   rather than assuming they match. Stop for any table without a verified time
   column or with a different type.
3. Choose and record one cutoff timestamp in UTC: the cleanup execution time
   minus 30 days. Use that same literal in every preview and deletion statement.
4. Preview the eligible row count in each table. For spans, logs, and metrics
   whose `time` column was verified as a timestamp, use:

   ```sql
   SELECT count(*) AS eligible_rows
   FROM uscentral.<verified_schema>.<exact_prefix>_otel_<verified_type>
   WHERE time < TIMESTAMP '<recorded UTC cutoff>';
   ```

   For annotations, inspect its schema and target semantics. If it has a
   `target_id` matching trace IDs or span IDs, count annotations linked to
   traces whose latest span is older than the cutoff:

   ```sql
   WITH expired_traces AS (
     SELECT trace_id
     FROM uscentral.<verified_schema>.<exact_prefix>_otel_spans
     GROUP BY trace_id
     HAVING max(time) < TIMESTAMP '<recorded UTC cutoff>'
   ), expired_targets AS (
     SELECT trace_id AS target_id FROM expired_traces
     UNION
     SELECT spans.span_id AS target_id
     FROM uscentral.<verified_schema>.<exact_prefix>_otel_spans AS spans
     JOIN expired_traces USING (trace_id)
   )
   SELECT count(*) AS eligible_rows
   FROM uscentral.<verified_schema>.<exact_prefix>_otel_annotations
   WHERE target_id IN (SELECT target_id FROM expired_targets);
   ```

   Use that annotation query only after confirming the actual `target_id`,
   `trace_id`, `span_id`, and `time` columns. Otherwise stop and determine the
   correct relationship from the current schema before proceeding.
5. Review every count and the exact catalog/schema/table shown by the SQL
   editor. If the query is broader than the three Fleet prefixes or the counts
   are unexpected, stop. Do not proceed on a failed, partial, or stale preview.

## Governed deletion

After reviewing the dry-run counts, delete annotations first, then logs,
metrics, and spans. For each prefix, use the exact verified fully qualified
table names and the same recorded cutoff. The following are templates only;
substitute values only after the preflight checks above:

```sql
WITH expired_traces AS (
  SELECT trace_id
  FROM uscentral.<verified_schema>.<exact_prefix>_otel_spans
  GROUP BY trace_id
  HAVING max(time) < TIMESTAMP '<recorded UTC cutoff>'
), expired_targets AS (
  SELECT trace_id AS target_id FROM expired_traces
  UNION
  SELECT spans.span_id AS target_id
  FROM uscentral.<verified_schema>.<exact_prefix>_otel_spans AS spans
  JOIN expired_traces USING (trace_id)
)
DELETE FROM uscentral.<verified_schema>.<exact_prefix>_otel_annotations
WHERE target_id IN (SELECT target_id FROM expired_targets);

DELETE FROM uscentral.<verified_schema>.<exact_prefix>_otel_logs
WHERE time < TIMESTAMP '<recorded UTC cutoff>';

DELETE FROM uscentral.<verified_schema>.<exact_prefix>_otel_metrics
WHERE time < TIMESTAMP '<recorded UTC cutoff>';

DELETE FROM uscentral.<verified_schema>.<exact_prefix>_otel_spans
WHERE time < TIMESTAMP '<recorded UTC cutoff>';
```

Repeat the preview after deletion and record the run date, cutoff, exact table
names, deleted counts, and operator. Use the workspace's governed SQL warehouse
and approved change record. Do not run a recurring job as part of this
procedure. Unity Catalog trace deletion is performed through governed SQL;
MLflow does not provide an API for deleting these rows. Follow the current
[Databricks trace table guidance](https://docs.databricks.com/gcp/en/mlflow3/genai/tracing/trace-unity-catalog)
and [trace governance guidance](https://docs.databricks.com/gcp/en/mlflow3/genai/tracing/govern-redact)
before each cleanup because table schemas and provider retention behavior can
change.

## Preflight validation record — 2026-10-17

Under the agreed live-acceptance cap, Genie checked the exact 12 tables named
above through `uscentral.information_schema.columns`. All 12 tables existed.
The spans, logs, and metrics tables exposed `time TIMESTAMP`; annotation tables
exposed `target_type` and `target_id` and no `time` column, so linked-target
counts were used. With the single UTC cutoff `2026-09-17 06:31:35`, the dry-run
count was zero for spans, logs, metrics, and linked annotations for each of the
runtime, evaluation, and optimization prefixes. No trace data was deleted.
The preview returned no row payloads. This is a point-in-time preflight, not a
retention guarantee; a later manual run must repeat schema and count checks.
