# Evaluation and Monitoring

The Databricks-backed quality loop runs server-side against the managed
`fleet_turn` traces written by a Databricks-tracing policy (a local profile
with `mlflow.tracking_uri = "databricks"` and the UC `mlflow.*_env` references).
It adds
no Fleet Turn-path surface: tracing stays fail-soft and bounded by the
`_sanitize_mlflow_span` security export boundary, and every step is an opt-in script
behind `FLEET_LIVE=1` with Databricks auth from the environment
(`DATABRICKS_HOST`/`DATABRICKS_TOKEN` or databricks-cli).

## Pipeline at a glance

| Step | Script | Receipt schema |
| --- | --- | --- |
| Dataset management | `scripts/benchmarks/rlm_eval_dataset.py` | `fleet.eval-dataset/v1` |
| Production monitoring | `scripts/benchmarks/enable_monitoring.py` | `fleet.monitoring-config/v1` |
| Judge alignment | `scripts/benchmarks/align_judges.py` | `fleet.judge-alignment/v1` |
| Trace attribute annotation | `scripts/benchmarks/annotate_traces.py` | `fleet.trace-annotation/v1` |
| Prompt registry + trace linking | `scripts/benchmarks/manage_prompts.py` | `fleet.prompt-registry/v1` |
| Evaluation scorers | `scripts/benchmarks/scorers.py` (via `run_rlm_latency.py evaluate --scorers`) | `fleet.rlm-latency/v1` |

Prerequisites: the `benchmark` extra (`uv sync --extra benchmark`) provides
`databricks-agents` for UC-managed datasets. Production monitoring requires a
Databricks-tracing policy (UC trace destination via the profile's
`mlflow.*_env` references) in an eligible region with the
OpenTelemetry-on-Databricks preview enabled.

**Databricks vs local MLflow (`http://localhost:5001`).** Follow the traces,
keep quality-of-record in UC, keep fast loops local: R1 monitoring, R4
alignment, and R3 production-trace ingestion run **Databricks-only**
(server-side scorers on UC traces, SME sessions, UC OTEL reads). The quality
gate runs on **either**; for local runs pass
`--mlflow-url http://127.0.0.1:5001` with `--experiment-id 1` and use the
entity-store dataset from `prepare-evaluation` (works without
`databricks-agents`). `gateway:/` judge
routes (`gateway:/databricks-inkling`, the local AI Gateway endpoint) require
the local HTTP(S) tracking server; `databricks:/...` serving endpoints are
reachable from both.

**Python 3.13 constraint: UC dataset reads/writes run in a Python 3.12 lane.**
`mlflow.genai.datasets` on the Databricks backend requires `databricks-agents`,
which imports `pyspark`, and the UC sync runs through Spark Connect
(`databricks-connect==18.0.0`, serverless kernel v5) — both are capped at
Python 3.12 while the project env is 3.13. Run dataset management through the
detached ephemeral lane and consume exported records in the 3.13 lane:

```bash
uv run --no-project --python 3.12 \
  --with 'mlflow[genai]==3.16.0' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py <ingest-static|ingest-traces|show|export|history|tag> ...
```

Use `export --export-out records.json` to materialize records for downstream
evaluation on any interpreter.

## 1. Dataset: expectation-bearing records (`fleet-rlm-quality-v2`)

```bash
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]==3.16.0' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py ingest-static \
  --experiment-id <id> --output .scratch/evals/dataset-static.json
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]==3.16.0' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py ingest-traces \
  --experiment-id <id> --expectations-json .scratch/evals/expectations.json \
  --output .scratch/evals/dataset-traces.json
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]==3.16.0' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py show \
  --experiment-id <id> --output .scratch/evals/dataset-show.json
```

- `ingest-static` creates the managed dataset when missing and seeds the five
  static cases only when the dataset is empty; any existing rows skip the merge
  entirely (`--force` re-merges all five regardless).
- `ingest-traces` merges production traces tagged `fleet_eval_candidate=true`;
  every merged record needs an entry in the expectations mapping
  (`{"trace_id": {"expected_response": ..., "required_evidence": [...], ...}}`),
  and trace ids already present are skipped so the command is idempotent.
- Both ingest commands accept `--dataset-tags "key=value,key=value"` to stamp
  dataset-level tags after the merge (e.g. `fleet.source=static,fleet.version=v2.1`).
- `history --name-prefix <prefix>` lists managed datasets with id, name, created
  time, record presence, and up to 32 tags; `tag --tag-key <k> --tag-value <v>`
  sets a dataset-level tag and `tag --tag-key <k> --delete` removes one.
- Records keep the strict shape `{"inputs": {"query": ...},
  "expectations": {...}}`; trace-origin records carry
  `expectations.source_trace_id` for lineage.

## 2. Production monitoring scorers

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/enable_monitoring.py start \
  --experiment-name fleet-rlm --sample-rate 0.1 \
  --output .scratch/evals/monitoring-start.json
FLEET_LIVE=1 uv run python scripts/benchmarks/enable_monitoring.py status \
  --experiment-name fleet-rlm --output .scratch/evals/monitoring-status.json
```

`start` registers the built-in `safety` scorer (sampled at 1.0) and starts the
Fleet `correctness` + `evidence_coverage` judges at `--sample-rate` (default
0.1). Monitoring executes inside Databricks on ingested traces and never
changes Turn outcomes. `stop` halts scoring without deleting registrations.

## 3. Judge alignment with MemAlign

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/align_judges.py prepare-labeling \
  --experiment-name fleet-rlm --assigned-users sme1@example.com sme2@example.com \
  --api-url http://127.0.0.1:8000 --output .scratch/evals/prepare-labeling.json
# SMEs label traces in the printed session URL, then:
FLEET_LIVE=1 uv run python scripts/benchmarks/align_judges.py align \
  --experiment-name fleet-rlm --output .scratch/evals/align.json
FLEET_LIVE=1 uv run python scripts/benchmarks/align_judges.py reeval-baseline \
  --experiment-name fleet-rlm --api-url http://127.0.0.1:8000 \
  --prior-metrics .scratch/evals/prior-quality.json \
  --output .scratch/evals/aligned-baseline.json
```

- `prepare-labeling` evaluates the five static cases with the base judges,
  tags OK traces (`eval=complete`), builds the labeling dataset, creates
  pass/fail label schemas **named exactly like the judges**
  (`correctness`/`evidence_coverage`), and opens an SME session.
- `align` distills SME feedback with `MemAlignOptimizer` (reflection:
  `databricks:/system.ai.claude-opus-4-8`; embeddings:
  `databricks:/databricks-gte-large-en`) and updates each aligned judge with
  its existing sampling configuration. Active scorers keep their sample rate,
  paused scorers keep `0.0`, and unconfigured scorers remain unconfigured;
  monitoring picks up the aligned version in place, with no restart. The
  alignment receipt records the prior/resulting sample rate and filter,
  aligned version when available, and `monitoring_state`.
- Aligned judges may score **lower** than unaligned ones; that is the judge
  matching expert standards, not an agent regression.

## 4. Trace annotation, prompt registry, and evaluation scorers

### 4.1 Trace attribute annotation (`annotate_traces.py`)

Persisted `fleet_turn` traces carry rich span structure but only a small fixed
set of runtime tags. `annotate` walks traces and stamps derived, non-content
`fleet.*` trace tags — `fleet.turn_status`, `fleet.latency_ms`,
`fleet.models`, `fleet.providers`, `fleet.tools`, `fleet.total_tokens`,
`fleet.span_types` — so the MLflow UI and `search_traces(filter_string=...)`
can select by model, provider, tool, latency, or token usage. Values are
bounded aggregates; prompts, responses, and span content are never exported.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/annotate_traces.py annotate \
  --experiment-name fleet-rlm --limit 100 \
  --tag fleet_eval_candidate \
  --output .scratch/evals/annotate.json
```

Optional `--tag <key>` restricts annotation to traces carrying
`tag.<key>='true'` (e.g. the eval-candidate tag). This is a post-hoc,
scripts-only tagger: it never changes span emission in
`src/fleet_rlm/observability/tracing.py`.

### 4.2 Prompt registry and trace linking (`manage_prompts.py`)

`register` versions the `FleetRLMSignature` instruction text (or a
`--text-file`) as an MLflow prompt tagged with `fleet.source` and
`fleet.signature_sha256`; `link-traces` maps tagged traces to a registered
version for lineage; `list` / `set-alias` wrap `search_prompts` /
`set_prompt_alias`.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/manage_prompts.py register \
  --experiment-name fleet-rlm --prompt-name fleet-rlm-signature \
  --alias latest --output .scratch/evals/prompt-register.json
FLEET_LIVE=1 uv run python scripts/benchmarks/manage_prompts.py link-traces \
  --experiment-name fleet-rlm --prompt-name fleet-rlm-signature --version 1 \
  --tag fleet_eval_candidate --output .scratch/evals/prompt-link.json
FLEET_LIVE=1 uv run python scripts/benchmarks/manage_prompts.py list \
  --output .scratch/evals/prompt-list.json
```

### 4.3 Evaluation scorers (`scorers.py`)

`scorers.py` adds deterministic custom scorers and MLflow built-ins on top of
the `correctness` / `evidence_coverage` judges:

- `response_present` — the response is a non-empty answer string.
- `tool_evidence_used` — the trace's tool spans cover every
  `expectations.required_evidence` item (strict: returns `False` when no trace
  is available).
- `guidelines` (built-in) and `retrieval_groundedness` (built-in) — LLM-based,
  require the `--judge-model` URI.

Wire them into the quality gate without changing default behavior:

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py evaluate \
  --experiment-id 1 --mlflow-url http://127.0.0.1:5001 \
  --scorers response_present,tool_evidence_used,guidelines \
  --guidelines 'The response must stay within the requested scope.' \
  --output .scratch/evals/quality-scorers.json
```

`--scorers` is additive; omitting it keeps the current two-judge default, and
the receipt lists the applied `scorers` by name.

### Rationale-first judge experiment

MLflow 3.16 can ask a judge to generate a rationale before its final
assessment. Fleet keeps that setting opt-in and isolates the comparison from
the canonical `correctness` and `evidence_coverage` registrations. Run both
variants against the same frozen dataset frame in a separate evaluation
experiment:

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py evaluate \
  --experiment-id <dataset-experiment-id> \
  --evaluation-experiment-id <isolated-evaluation-experiment-id> \
  --mlflow-url http://127.0.0.1:5001 \
  --judge-model <mlflow-supported-judge-uri> \
  --judge-ab \
  --output .scratch/evals/judge-ab.json
```

The command constructs baseline and rationale-first scorers in memory, so it
does not call canonical judge registration or change shared scorer versions.
Its bounded receipt records the dataset snapshot, model and normalized judge
policies, instructions, inference parameters, rationale setting, scorer
values, latency, and any token/cost measurements MLflow exposes. It reports
per-judge agreement and bounded disagreements for the same inputs. Accuracy is
left unset unless independent reference labels are supplied; evaluation
expectations are not independent labels. User satisfaction feedback from the
TUI is a separate `user_feedback` assessment and must not be merged into
correctness or evidence-coverage labels.

Keep `generate_rationale_first=False` for the normal registered judges. After
an explicit promotion decision, register a new version with
`ensure_registered(..., generate_rationale_first=True)` and review the
normalized policy diff, including the rationale setting, before changing any
active monitoring or evaluation configuration. The A/B command itself never
promotes a scorer.

### Trace V4 navigation

For a compact operational view in MLflow, include the existing Fleet fields
`fleet.session_id`, `fleet.trace_phase`, `fleet.run_id`, `fleet.turn_status`,
`fleet.latency_ms`, `fleet.models`, `fleet.providers`, `fleet.tools`,
`fleet.total_tokens`, `fleet.cache_read_tokens`, and
`fleet.cache_creation_tokens`. Keep Trace ID, status, latency, and error state
in the primary columns, then use the session and phase fields to follow a
Turn. Local supervised traces expose a one-way preparation-to-execution Span
Link; Unity Catalog traces retain tag-based correlation through
`fleet.preparation_trace_id`.

## Failure and budget guardrails

- Every script writes a bounded JSON receipt and exits non-zero on failure;
  `error_category` carries only the exception type name.
- Monitoring amplifies judge-token cost by the sample rate; keep Fleet judges
  near 0.1 and `safety` at 1.0 unless a regression hunt justifies more.

## Validation

```bash
uv run pytest tests/unit/optimization tests/unit/scripts/test_align_judges.py \
  tests/unit/scripts/test_enable_monitoring.py \
  tests/unit/scripts/test_rlm_eval_dataset.py \
  tests/unit/scripts/test_annotate_traces.py \
  tests/unit/scripts/test_manage_prompts.py \
  tests/unit/scripts/test_scorers.py -q
```

### Isolate related optimization examples

Curated export provenance may include opaque `session_id` and `project_id`
identifiers. The existing splitter keeps connected Session/project groups in one
partition, including transitive relationships. It targets 60/20/20 proportions,
but group isolation and at least five examples per partition take precedence.
Exports that cannot meet those constraints are rejected; they are never split
across a shared Session/project to fill a quota. Exports without those identities
retain the existing record-based seeded partitioning.

Split manifests record the grouping policy and a digest of all validated record
content, so changing expectations changes the dataset identity even when record
IDs remain stable. Group identity values and sealed-test content remain absent
from the public manifest. These mechanics prevent declared-group leakage; they
do not certify semantic independence of examples whose provenance omits a shared
source.
