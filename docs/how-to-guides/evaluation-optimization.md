# Evaluation, Monitoring, and Signature Optimization

The Databricks-backed quality loop runs server-side against the managed
`fleet_turn` traces already written by the `daytona-managed` profile. It adds
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
| Signature optimization | `scripts/optimize/optimize_signature_omni.py` | `fleet.signature-optimization/v1` |
| Trace attribute annotation | `scripts/benchmarks/annotate_traces.py` | `fleet.trace-annotation/v1` |
| Prompt registry + trace linking | `scripts/benchmarks/manage_prompts.py` | `fleet.prompt-registry/v1` |
| Evaluation scorers | `scripts/benchmarks/scorers.py` (via `run_rlm_latency.py evaluate --scorers`) | `fleet.rlm-latency/v1` |

Prerequisites: the `benchmark` extra (`uv sync --extra benchmark`) provides
`databricks-agents` for UC-managed datasets; the `optimize` extra provides
`gepa`. Production monitoring requires the managed tracing profile (UC trace
destination: `config/fleet.toml` `[profiles.daytona-managed.mlflow]`) in an
eligible region with the OpenTelemetry-on-Databricks preview enabled.

**Databricks vs local MLflow (`http://localhost:5001`).** Follow the traces,
keep quality-of-record in UC, keep fast loops local: R1 monitoring, R4
alignment, and R3 production-trace ingestion run **Databricks-only**
(server-side scorers on UC traces, SME sessions, UC OTEL reads). The R5
optimizer and the R2 quality gate run on **either**; for local runs pass
`--mlflow-url http://127.0.0.1:5001` with `--experiment-id 1` and use
`--dataset-json` (hermetic records) or the entity-store dataset from
`prepare-evaluation` (works without `databricks-agents`). `gateway:/` judge
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
  --with 'mlflow[genai]>=3.15' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py <ingest-static|ingest-traces|show|export|history|tag> ...
```

Use `export --export-out records.json` to materialize records for
`--dataset-json` runs of the optimizer on any interpreter.

## 1. Dataset: expectation-bearing records (`fleet-rlm-quality-v2`)

```bash
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]>=3.15' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py ingest-static \
  --experiment-id <id> --output .scratch/evals/dataset-static.json
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]>=3.15' --with 'databricks-agents>=1.11' \
  --with 'databricks-connect==18.0.0' --with httpx --with python-dotenv \
  python scripts/benchmarks/rlm_eval_dataset.py ingest-traces \
  --experiment-id <id> --expectations-json .scratch/evals/expectations.json \
  --output .scratch/evals/dataset-traces.json
FLEET_LIVE=1 uv run --no-project --python 3.12 \
  --with 'mlflow[genai]>=3.15' --with 'databricks-agents>=1.11' \
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

## 4. Signature optimization (GEPA omni composition)

### Script-only optimizer lane

Optimization remains a trusted-host CLI workflow. API and TUI integration is
deferred until the strict production evaluator, trusted judges, sealed-test
ledger, cost accounting, and manual-review workflow are complete. The supported
interfaces are the scripts under `scripts/optimize/`; receipts and candidates
are immutable local evidence under `.scratch/optimization/<run-id>/` and are
never consumed by the runtime automatically.

The same loop runs headless:

```bash
uv sync --extra optimize
FLEET_LIVE=1 uv run python scripts/optimize/optimize_signature_omni.py \
  --dataset-name fleet-rlm-quality-v2 --val-fraction 0.2 \
  --judge-model databricks:/databricks-qwen35-122b-a10b \
  --judge-params '{"temperature": 0}' \
  --output .scratch/optimization/receipt.json
```

**Judge model calibration practices (required for the cheap lane):**

- Judge identity is part of the metric. Verified serving-endpoint judge
  candidates (2026-08 probes, 4-case evidence fixture):

  | Judge | Fixture | Params | Lane |
  | --- | --- | --- | --- |
  | `databricks:/databricks-qwen35-122b-a10b` | 4/4 | `{"temperature": 0}` or defaults | primary cheap judge |
  | `databricks:/databricks-gemini-3-5-flash` | 3/4 (coverage strict) | `{"temperature": 0}` | cost floor |
  | `databricks:/databricks-claude-sonnet-4-6` | passes | `{"temperature": 0}` | final-gate ceiling |

- `databricks-inkling` (`correctness`-only with
  `{"temperature": 0, "reasoning_effort": "none"}`), `deepseek-v4-pro`
  (rejects structured verdict responses), `claude-haiku-4-5`, `gpt-oss-120b`,
  and `gpt-5-mini` (temperature-locked) are unsuitable — recheck probes on any
  model change.
- Generational independence: the maintained Oolong mechanics profile uses
  `deepseek-v4-flash` for both agent roles. A qwen35 judge is therefore
  cross-family, but judge identity must still remain fixed within one metric
  history.
- Judge identity is not interchangeable mid-history: pick one family per
  metric track and keep receipts comparable to it. On judge-family switch,
  run one calibration pass over the static records (score arm / `evaluate`)
  and file the baseline receipt before spending optimization budget.
- `policy` judges can be retuned with `--judge-params '{"temperature": 0}'`
  for serving endpoints that reject the AI-Gateway `reasoning_effort` knob.

- The candidate artifact is the `FleetRLMSignature` instruction text
  (`src/fleet_rlm/rlm/signature.py`). Evaluation Turns run on the AI Gateway
  WORKER/FAST tiers; reflection proposals run on the reserved FRONTIER tier
  (`system.ai.claude-opus-4-8` by default, see `src/fleet_rlm/rlm/lm_factory.py:153`).
- `--engine auto` (default) runs the **native omni pipeline**: `optimize_best_of`
  across the `gepa`, `meta_harness`, and `autoresearch` engines on
  `--explore-metric-calls` per engine, then a fresh `gepa` continue at
  `--continue-metric-calls`. The agent engines shell out to the local `claude`
  CLI (`--agent-model`/`--agent-effort`). `--engine gepa` pins the
  single-engine fallback composition; `--engine autoresearch|meta_harness`
  runs that agent engine alone. gepa is pinned via
  `[tool.uv] override-dependencies` to the omni commit; `--engine auto`
  degrades to the fallback composition if the registry is unavailable.
- The score is cost-Pareto shaped: each REPL iteration subtracts
  `--cost-penalty-per-iteration` (default `0.005`, `0` disables), so cheap
  Turns dominate quality ties; the receipt reports `objective_pareto_size`
  when the engine exposes the Pareto front.
- `--scorer-source registry` (default) scores with the experiment's registered
  (aligned) judges; `policy` builds the base judges locally from
  `--judge-model`.
- The best candidate text and its SHA-256 land in the receipt and
  `.scratch/optimization/candidate-*.txt`. **Promotion is manual**: a human
  reviews the diff and edits `src/fleet_rlm/rlm/signature.py`; the runtime
  never consumes optimizer output directly.
- `--register-prompt` (with `--prompt-name`, `--prompt-alias`,
  `--prompt-commit-message`) registers the best candidate as a versioned MLflow
  prompt through the shared prompt-registry core; the receipt records
  `prompt_registry.prompt_version` for lineage. Registration never changes the
  manual promotion rule — the runtime still only consumes
  `src/fleet_rlm/rlm/signature.py`.
- The default executor runs candidate REPL code in-process; run optimization
  only on trusted hosts.

### Strict Daytona evaluator admission remains blocked

The future strict evaluator is not enabled by an optimizer run. Production
admission requires a validated live proof for the exact trusted snapshot and
exact stable gateway domain policy. Daytona's domain allow-list mode is used by
itself: do not combine it with `network_block_all` or a CIDR allow list.

For a quick development-only smoke, the wrapper starts a loopback synthetic
controller and two temporary Cloudflare quick-tunnel HTTPS origins. No domain,
DNS record, controller credential, Fleet credential, or provider credential is
configured in Daytona; the sandbox receives only the two synthetic public URLs,
a random non-secret path nonce, and a per-run gateway-broker capability.

```zsh
FLEET_LIVE=1 uv run python scripts/live_daytona_tunnel_probe.py -- \
  uv run pytest tests/live/backend/test_safe_gepa_daytona_policy.py -q
```

Quick Tunnels are subject to Cloudflare's development limits. A developer may
instead configure a local named development tunnel with two dedicated HTTPS
hostnames. Set all four local-only variables before the same command:

```zsh
export FLEET_OPTIMIZATION_NAMED_TUNNEL_ID=<tunnel-id>
export FLEET_OPTIMIZATION_NAMED_TUNNEL_CREDENTIALS_FILE=<local-credentials-file>
export FLEET_OPTIMIZATION_NAMED_TUNNEL_ALLOWED_ORIGIN=https://<allow-hostname>
export FLEET_OPTIMIZATION_NAMED_TUNNEL_DENIED_ORIGIN=https://<deny-hostname>
```

The named-tunnel credential is consumed only by the wrapper and is removed
from the child test environment. Both origins must be distinct bare HTTPS
origins; the wrapper writes its dynamic loopback ingress configuration to a
temporary file and removes it after the command. This reliable development
mode remains synthetic and non-authorizing.

The wrapper preflights both origins, then injects its loopback controller URL
and random bearer token only into the local pytest process. The sandbox invokes
`read_curated_input` through a dedicated path on the already-allowlisted
gateway; the controller only relays claimed calls to the host and does not log
request bodies. The live test allows the first temporary hostname, attempts an
allow-to-deny redirect, direct denied request, and synthetic-marker denied POST,
and requires zero denied deliveries after the observation window. It also
validates broker capability, no volume, ephemeral sandbox policy, absence of
Fleet/provider/model credential variables, and Daytona not-found after deletion.

This writes a sanitized `fleet.daytona-development-canary/v1` report containing
only a policy digest, snapshot, and pass/fail controls. It deliberately omits
tunnel URLs and cannot be converted into `ValidatedStrictDaytonaProof`; it does
not authorize production GEPA.

The separate synthetic GEPA smoke does not create a Daytona sandbox and does
not need the tunnel configuration. Its CLI reads the repository `.env` with the
dotenv parser, so do not `source .env` in zsh. Evidence directories are
write-once: use a new run ID for every invocation.

```zsh
FLEET_LIVE=1 uv run python scripts/optimize/optimize_signature_gepa.py development-smoke \
  --export-json .scratch/optimization/development-synthetic-export.json \
  --split-seed 0 \
  --max-total-cost-usd 0.10 \
  --max-evals 2 \
  --run-id "development-gepa-smoke-$(date +%Y%m%d-%H%M%S)"
```

Production remains blocked until the actual stable trusted gateway FQDN is
tested under its exact policy and Daytona confirms that organization-tier
essential-service exceptions cannot provide a general exfiltration path.

## 5. Trace annotation, prompt registry, and evaluation scorers

### 5.1 Trace attribute annotation (`annotate_traces.py`)

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
`src/fleet_rlm/observability/turn_tracing.py`.

### 5.2 Prompt registry and trace linking (`manage_prompts.py`)

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

The optimizer's `--register-prompt` reuses the same core
(`manage_prompts.register_prompt_text`), so promoted GEPA candidates and
manually registered signatures land in one registry. Linking to the experiment
is best-effort and backend-dependent; registration succeeds regardless.

### 5.3 Evaluation scorers (`scorers.py`)

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

## Failure and budget guardrails

- Every script writes a bounded JSON receipt and exits non-zero on failure;
  `error_category` carries only the exception type name.
- Executor failures during optimization score 0 with the bounded failure
  category as reflection side information — the optimizer learns from
  structural failures instead of crashing.
- Monitoring amplifies judge-token cost by the sample rate; keep Fleet judges
  near 0.1 and `safety` at 1.0 unless a regression hunt justifies more.

## Validation

```bash
uv run pytest tests/unit/optimization tests/unit/scripts/test_align_judges.py \
  tests/unit/scripts/test_enable_monitoring.py \
  tests/unit/scripts/test_rlm_eval_dataset.py \
  tests/unit/scripts/test_optimize_signature_gepa.py \
  tests/unit/scripts/test_optimize_signature_omni.py \
  tests/unit/scripts/test_annotate_traces.py \
  tests/unit/scripts/test_manage_prompts.py \
  tests/unit/scripts/test_scorers.py -q
```
