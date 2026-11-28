# Fleet-RLM architecture

## HTTP application

The [application entry point](../backend/src/fleet_rlm/api.py) assembles routers, starts the runtime, and serves health probes. The [HTTP package](../backend/src/fleet_rlm/http/) owns request access and the chat, workspace, settings, provider, and optimization routes. The workspace bearer check runs before request bodies are read. Provider administration uses the separate administrator bearer.

## Chat and workspace

The [React chat hook](../frontend/src/hooks/use-session-chat.ts) posts through DefaultChatTransport. The [chat router](../backend/src/fleet_rlm/http/chat.py) admits one running root per session, hydrates durable files and calls the [agent](../backend/src/fleet_rlm/agent.py). Each RLM invocation receives scoped tools, model selection and an interpreter factory. Children run with their own interpreter. The [stream mapper](../backend/src/fleet_rlm/ui_stream.py) produces AI SDK frames, and FastAPI sends JSON events followed by raw `[DONE]`. MLflow correlates the turn and children using Fleet run identifiers.

The session messages endpoint returns a chronological array. Supply `limit` to request the newest 1 to 50 root turns and follow the `Link` header to older turns. Supplying an exclusive `before` root ID alone requests up to 25 turns. An unpaged call returns the full transcript.

The [database](../backend/src/fleet_rlm/db.py) owns sessions, root and child runs, file manifests, outstanding object cleanup, memories and workspace settings. Transactions fence publication, deletion and recovery. The [Daytona interpreter](../backend/src/fleet_rlm/interpreter.py) owns the VM and its scratch files. The [host storage adapter](../backend/src/fleet_rlm/storage.py) retains inputs and published artifacts across VM replacement. The manifest identifies the verified bytes belonging to each session. See the [locking](../backend/tests/test_phase1_locking.py), [engine](../backend/tests/test_phase2_engine.py) and [HTTP tests](../backend/tests/test_phase3_api.py).

The [ChatGPT plan connection](../backend/src/fleet_rlm/chatgpt_plan.py) keeps OAuth grants in `~/.config/fleet-rlm/chatgpt/credentials.json` and model defaults in `~/.config/fleet-rlm/chatgpt-models/defaults.json` on the backend host. Both files are owner-only. Fleet imports older saved defaults on first use. Session model references identify a local registration and model. The host resolves its grant when that model is called and streams inference from OpenAI into DSPy.

Fleet uses Sign in with ChatGPT’s open-source plan authorization for those model calls. The website sign-in flow provides identity for an application’s own login and sessions. [Plan authorization](https://developers.openai.com/siwc/token-sharing-open-source) · [Website sign-in](https://developers.openai.com/siwc/website)

## Optimization

The optimization API resolves roles and validates datasets. Postgres owns provider records, dataset metadata, jobs and promotions. A partial unique index fences active jobs by target. The [supervisor](../backend/src/fleet_rlm/opt_runner.py) holds process-local task handles, checks durable cancellation requests, prepares the dataset, and starts the [offline CLI](../backend/src/fleet_rlm/optimize.py). Built-in datasets ship with the application; custom datasets use host-side object storage or a configured Daytona Volume. The supervisor persists progress and terminal results. The browser reads named snapshot, update and done events and can poll for job state.

Promotion selects eligible saved artifacts for future turns. Serving restores instructions with request-local tools and model selection. Keep evaluation-only Flex runs separate from serving RLM artifacts. See [job and promotion tests](../backend/tests/test_optimization_ui_api.py) and [optimizer tests](../backend/tests/test_phase5_optimize.py).

## Tracing

The [Fleet capture policy](../backend/src/fleet_rlm/tracing.py) keeps DSPy autologging under one independent `fleet.turn` per admitted chat. The root has a 768-byte request and response preview, MLflow session metadata, and searchable `fleet.run_id`, `fleet.trace.kind`, `fleet.outcome` and `fleet.error.stage` tags. The separate `fleet.memory.consolidate` trace carries the same run and session IDs and a `fleet.parent_trace_id` when the chat trace was recorded. Lookup by the SSE-advertised trace ID verifies asynchronous delivery.

Interactive traces keep redacted model messages and replies plus tool arguments and results, including Python code and output. Each input and output is capped at 16 KiB of serialized UTF-8. Wrapper and adapter spans carry structure and timing. The callback attaches reported token usage to model spans for MLflow to aggregate. DSPy cache hits contribute no billed tokens, and unavailable usage is marked on the model span. Fleet spans record error classes and omit exception bodies, stacks, and binary attachments.

API optimization jobs use `<chat-experiment>-optimization`. The standalone optimizer still uses its configured `FLEET_MLFLOW_EXPERIMENT`. For replay of synthetic chat fixtures, set `FLEET_MLFLOW_FULL_TRACE_CAPTURE=true` in a backend process with a dedicated `FLEET_MLFLOW_EXPERIMENT`. Full capture retains the DSPy task, context and outputs needed to reconstruct examples. To include raw Daytona SDK transport spans in that fixture experiment, also set `FLEET_DAYTONA_OTEL_ENABLED=true` and `MLFLOW_USE_DEFAULT_TRACER_PROVIDER=false`. Interactive MLflow uses Fleet's Daytona spans, with sanitized failure details.

## Verification and operations

[CircleCI](../.circleci/continue-config.yml) runs both package checks for pull requests and main. The [release pipeline](../.circleci/deploy.yml) checks the built wheel and public PyPI download. See the [migration runbook](daytona-v2-migration.md) for staged recovery and the [evaluation baseline](evaluation-baseline.md) for dated model results.
