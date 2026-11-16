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

| Purpose | Catalog | Schema | Prefix |
|---|---|---|---|
| Runtime traces | `example_catalog` | `fleet_observability` | `fleet_rlm_runtime` |
| Evaluation traces | `example_catalog` | `fleet_observability` | `fleet_rlm_eval` |
| GEPA traces | `example_catalog` | `fleet_gepa` | `fleet_rlm_gepa` |

These values come from `config/fleet.toml.example`; the runtime prefix is also
`fleet_rlm_runtime` in the code fallback. If deployment overrides change these
values, use the effective configuration and verify each experiment's trace
location before cleanup. Genie metadata discovery
also found shared/base and legacy Fleet objects in these schemas. They are
outside this cleanup scope.
Each configured prefix can have `_otel_annotations`, `_otel_logs`,
`_otel_metrics`, and `_otel_spans` tables. Only operate on these three exact
Fleet prefixes. Do not use a schema-wide wildcard.

## Preflight and dry run

   ```sql
   SELECT count(*) AS eligible_rows
   FROM example_catalog.<verified_schema>.<exact_prefix>_otel_<verified_type>
   WHERE time < TIMESTAMP '<recorded UTC cutoff>';
   ```

   For annotations, inspect its schema and target semantics. If it has a
   `target_id` matching trace IDs or span IDs, count annotations linked to
   traces whose latest span is older than the cutoff:

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
   SELECT count(*) AS eligible_rows
   FROM example_catalog.<verified_schema>.<exact_prefix>_otel_annotations
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

## Preflight validation record — 2026-10-17

This historical record is sanitized: the deployment catalog is redacted.
Under the agreed live-acceptance cap, Genie checked the corresponding 12 tables
through the deployment catalog’s `information_schema.columns`. All 12 tables existed.
The spans, logs, and metrics tables exposed `time TIMESTAMP`; annotation tables
exposed `target_type` and `target_id` and no `time` column, so linked-target
counts were used. With the single UTC cutoff `2026-09-17 06:31:35`, the dry-run
count was zero for spans, logs, metrics, and linked annotations for each of the
runtime, evaluation, and optimization prefixes. No trace data was deleted.
The preview returned no row payloads. This is a point-in-time preflight, not a
retention guarantee; a later manual run must repeat schema and count checks.
