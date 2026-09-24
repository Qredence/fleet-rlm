---
name: data-analysis
description: Compute and verify descriptive statistics, trends, and qualified anomalies.
compatibility: Requires Fleet RLM variable mode with a Python interpreter.
metadata:
  version: "1.2.0"
  affordances:
    - artifacts.publish
    - llm_query_batched
allowed-tools: read_attachment

resources: []

---

# Data analysis

Use Python for deterministic calculations over authorized data. Never invent
Attachment IDs or host paths. Large intermediate tables may live in private
interpreter files; a child result file is an input candidate until the root
checks its source revision, coverage, and values.

## Prepare the data

1. Inspect the supplied schema, column types, row count, missing values,
   duplicates, and parse failures before computing results.
2. For an Attachment, call `read_attachment(attachment_id=...)` only with an
   ID present in the Turn's Attachment metadata. Require `ok: true`; use
   `content` when `encoding` is `utf-8`, and decode `content_base64` in memory
   when `encoding` is `base64`.
3. Keep the source and derived values in interpreter variables or files according
   to size and reuse. Do not treat a Python-local file as a durable Workspace file
   or Artifact.

## Analyze and verify

Compute only the requested metrics. State the statistical convention for every
ambiguous result, such as sample versus population dispersion, denominator,
time window, rounding, or missing-value treatment.

For anomaly claims:

1. State the rule used.
2. Report the relevant comparison value.
3. Do not call a point anomalous when it does not cross the rule.
4. For very small samples, qualify the conclusion and prefer descriptive
   language.

Before submitting, recompute and compare all reported counts, extrema, sums,
means, medians, dispersion values, and anomaly comparisons against the original
data. If the data is insufficient, say what is missing instead of guessing;
use empty `metrics` and `anomalies` lists when appropriate.

For a partitioned dataset, record processed and missing partitions. Use
`llm_query_batched` only for independent semantic classifications, validate
each returned item, then aggregate in Python. Report incomplete coverage and
unresolved child gaps explicitly.

Choose the work shape before calling a model:

- **Sparse:** filter deterministically to candidate rows, inspect their source
  values, and widen the search only if evidence conflicts or is incomplete.
- **Exhaustive:** enumerate all required partitions, retain one validated
  classification per source row, and report processed and missing counts.
- **Dependent:** resolve a required label or prerequisite first; compute the
  downstream metric only from verified results. Leave it unresolved when the
  prerequisite is missing.

Treat child files and findings as intermediate candidates. Check their source
revision, coverage, and values against the authorized input before including
them in a result.

## Submit

Use the exact fields accepted by the current `SUBMIT` binding and do not add
unrecognized fields. When the explicitly selected `data-analysis` schema is
active, submit exactly:

```python
SUBMIT(
    answer=answer,
    findings=findings,
    metrics=metrics,
    anomalies=anomalies,
)
```

Keep `answer` as a non-empty string, `findings` and `anomalies` as string lists,
and `metrics` as a list of objects with a stable `name` and `value` (plus a
unit or convention when useful). When the default Fleet Signature is active,
submit only its required `answer` field.
