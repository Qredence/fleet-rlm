# Evaluation and Optimization

Fleet keeps evaluation and performance work in explicit, bounded commands. The
maintained runners cover latency, routing behavior, Oolong scoring, quality
judges, and Phase 6 planning. Auxiliary dataset, prompt, annotation, monitoring,
and judge-alignment administration commands have been retired; MLflow tracing
and campaign receipts remain owned by the retained certification and benchmark
commands.

## Local evaluation and quality scoring

Prepare the canonical evaluation dataset and judges, then run a bounded local
MLflow evaluation against an existing Fleet API. `--dry-run` evaluates only the
first three dataset rows. Use a fresh receipt path for each attempt.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py prepare-evaluation \
  --mlflow-url http://127.0.0.1:5001 --experiment-id 1 \
  --judge-model "gateway:/<local-gateway-endpoint>" \
  --output .scratch/evals/local-prepare.json
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py evaluate \
  --mlflow-url http://127.0.0.1:5001 --experiment-id 1 \
  --api-url http://127.0.0.1:8000 \
  --judge-model "gateway:/<local-gateway-endpoint>" \
  --dry-run --scorers response_present,tool_evidence_used \
  --output .scratch/evals/local-evaluate.json
```

The canonical correctness and evidence-coverage judges are registered through
`judges.py`; its deterministic `response_present` and `tool_evidence_used`
scorers are opt-in additions. `tool_evidence_used` checks complete identifiers
in tool output. It does not prove semantic correctness or support.

## MLflow tracing verification

The smoke lane emits and flushes one trace using the currently selected Fleet
policy. The certification lane exercises bounded trace, feedback, lifecycle,
and fresh-process sampling contracts; `--backend configured` requires an
explicit configured tracking URI and managed trace-location settings. Neither
command changes the selected configuration.

```bash
uv run python scripts/benchmarks/certify_mlflow.py smoke
FLEET_LIVE=1 uv run python scripts/benchmarks/certify_mlflow.py certify \
  --backend local --tracking-uri http://127.0.0.1:5001 \
  --experiment-name fleet-mlflow-certification --fault-checks \
  --output .scratch/evals/local-certification.json
```

Sampling probes run in separate processes against the selected backend. A
complete certificate returns zero; incomplete evidence returns two. A passing
receipt describes only the checks it ran and does not establish promotion or
release readiness.

## Performance and Phase 6 planning

The latency runner keeps benchmark, evaluation, comparison, and Phase 6 options
separate. Offline plan, dry-run, and analysis commands do not contact Fleet,
MLflow, Daytona, or a model provider:

```bash
uv run python scripts/benchmarks/run_rlm_latency.py phase6-plan \
  --output .scratch/evals/phase6-plan.json
uv run python scripts/benchmarks/run_rlm_latency.py phase6-dry-run \
  --output .scratch/evals/phase6-dry-run.json
```

The frozen manifest records task families and input/rubric digests. Planning
receipts do not execute A/B/C arms, isolate Sessions, establish cold/warm
conditions, or prove quality. Provider-backed campaigns require explicit
operator authorization and declared budget, admission, duration, and cleanup
limits.

The routing runner supports credential-free plan receipts and a separately
authorized live matrix. The Oolong runner supports the fixed offline fixture
and explicit live dataset mode. Their receipts preserve the established
schemas and contain bounded measurements rather than prompts or model output.

## Strict GEPA evaluator network boundary

Production GEPA remains fail-closed until a live strict evaluator proof and a
complete trusted quality campaign are sealed. The selected boundary does not depend on a temporary tunnel or an
unowned public hostname: the host polls Fleet's authenticated retained broker,
and the disposable Daytona evaluator sandbox is created with
`network_block_all=true`, no gateway domain/CIDR allow-list, no volume, and an
ephemeral lifecycle. Host-owned LM and Tool mediation therefore stays outside
the sandbox's outbound network boundary. A production proof must use the
`fleet.strict-daytona-proof/v2` receipt and pass transport authentication,
essential-service, raw-socket, and DNS egress-denial probes in addition to the
existing broker, cleanup, and deletion checks. The development canary and any
temporary tunnel remain non-authoritative. The 2026-09-14 live receipt is stored
under `.fleet-evidence/receipts/phase6/strict-gepa-proof/`; it is evaluator-
boundary evidence only and does not authorize GEPA execution or promotion by
itself.

An authoritative GEPA run validates a compiled module by saving only its
DSPy state as JSON in a private temporary directory outside the evidence
store. It imports a caller-supplied baseline student factory in a fresh Python
process, loads the state with pickle disabled, and compares digests of the
complete named predictor state and instructions. The runner reconstructs that
state again for held-out evaluation; only bounded metrics and digests enter the
write-once receipt, and the temporary state file is removed when the run ends.
Factory paths use `module:qualname` and constructor arguments must be JSON
serializable and contain no credential-shaped fields. Production receipts
remain `promotion_eligible: false`; this check proves reconstruction, not
candidate quality, serving readiness, or promotion authority.

Strict evaluator cleanup is owned per disposable sandbox. Daytona deletion uses
`wait=True` with a bounded SDK timeout, so an accepted delete request is not
treated as destruction. The evaluator retains the sandbox identity until its
RLM worker finishes, the interpreter shutdown succeeds, and deletion is
confirmed (including explicit not-found). Timeout and cancellation start
deletion before draining the worker; a failed delete is retried after drain.
Call `StrictDaytonaEvaluationLifecycle.aclose()` when the owning campaign is
closing to drain supervised cleanup and retry unresolved sandboxes. An
unresolved cleanup raises with the sandbox identity and cannot produce success
evidence. Offline lifecycle tests establish this ownership behavior; they do
not certify live Daytona deletion semantics.

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

- Retained operators use bounded or write-once receipts; preserve failed receipts and choose a new output path for each retry.
- Campaign admission checks reserve worst-case spend and cleanup time before live work.
- Sampling, evaluation, and routing measurements do not independently authorize a policy change or promotion.

## Validation

```bash
uv run pytest tests/unit/scripts/test_judges.py \
  tests/unit/scripts/test_certify_mlflow.py \
  tests/unit/scripts/test_run_rlm_latency.py \
  tests/unit/scripts/test_run_routing_eval.py \
  tests/unit/scripts/test_run_oolong_predict.py -q
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

## Historical Phase 4 transport evidence

The Phase 4 campaign driver and its partial-live command have been retired.
Historical campaign descriptions and receipts remain evidence only; they are
not current operator instructions or completed value results. Use the Phase 6
runner and its current authorization gates described above.
