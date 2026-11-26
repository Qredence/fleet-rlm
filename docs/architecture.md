# Fleet-RLM architecture

## Chat and workspace

The [React chat hook](../frontend/src/hooks/use-session-chat.ts) posts through DefaultChatTransport. The [API](../backend/src/fleet_rlm/api.py) admits one running root per session, hydrates durable files and calls the [agent](../backend/src/fleet_rlm/agent.py). Each RLM invocation receives scoped tools, model selection and an interpreter factory. Children run with their own interpreter. The [stream mapper](../backend/src/fleet_rlm/ui_stream.py) produces AI SDK frames, and FastAPI sends JSON events followed by raw `[DONE]`. MLflow correlates the turn and children using Fleet run identifiers.

The [database](../backend/src/fleet_rlm/db.py) owns sessions, root and child runs, file manifests, outstanding object cleanup, memories and workspace settings. Transactions fence publication, deletion and recovery. The [Daytona interpreter](../backend/src/fleet_rlm/interpreter.py) owns the VM and its scratch files. The [host storage adapter](../backend/src/fleet_rlm/storage.py) retains inputs and published artifacts across VM replacement. The manifest identifies the verified bytes belonging to each session. See the [locking](../backend/tests/test_phase1_locking.py), [engine](../backend/tests/test_phase2_engine.py) and [HTTP tests](../backend/tests/test_phase3_api.py).

## Optimization

The optimization API resolves roles and validates datasets. Postgres owns provider records, dataset metadata, jobs and promotions. A partial unique index fences active jobs by target. The [supervisor](../backend/src/fleet_rlm/opt_runner.py) holds process-local task handles, checks durable cancellation requests, prepares the dataset, and starts the [offline CLI](../backend/src/fleet_rlm/optimize.py). Built-in datasets ship with the application; custom datasets use host-side object storage or a configured Daytona Volume. The supervisor persists progress and terminal results. The browser reads named snapshot, update and done events and can poll for job state.

Promotion selects eligible saved artifacts for future turns. Serving restores instructions with request-local tools and model selection. Keep evaluation-only Flex runs separate from serving RLM artifacts. See [job and promotion tests](../backend/tests/test_optimization_ui_api.py) and [optimizer tests](../backend/tests/test_phase5_optimize.py).

## Verification and operations

[CircleCI](../.circleci/continue-config.yml) runs both package checks for pull requests and main. The [release pipeline](../.circleci/deploy.yml) checks the built wheel and public PyPI download. See the [migration runbook](daytona-v2-migration.md) for staged recovery and the [evaluation baseline](evaluation-baseline.md) for dated model results.
