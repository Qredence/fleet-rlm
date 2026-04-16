# fleet-rlm Concepts

`fleet-rlm` combines a ReAct chat orchestrator with recursive long-context
execution over a shared Daytona-backed interpreter runtime.

## Core Concepts

## 1. ReAct Chat Orchestrator

`RLMReActChatAgent` is the interactive orchestrator.

It:

- receives user requests from CLI/API/WS
- decides tool actions
- streams intermediate/final events
- maintains conversation and document context

## 2. Recursive Long-Context Execution

For deep tasks, runners and tools use DSPy RLM signatures and iterative sandbox execution.

Examples:

- `SummarizeLongDocument`
- `ExtractFromLogs`

## 3. Interpreter Runtime Backends

Interpreter backends provide isolated remote execution.

Benefits:

- sandbox isolation from host environment
- persistent storage integration when configured
- controlled execution profiles for root/delegate behavior

Current backend shape:

- Daytona is the maintained workspace/runtime backend
- the public product/runtime contract is Daytona-only
- the same ReAct + recursive `dspy.RLM` runtime serves the CLI, API, and web app

## 4. Runtime Surfaces

- Terminal chat: `fleet-rlm chat` or `fleet`
- Web/API: `fleet web` or `fleet-rlm serve-api`
- MCP: `fleet-rlm serve-mcp`

All surfaces converge on shared orchestration/runtime modules.

## 5. Observability and State

The system emits:

- chat stream events (`/api/v1/ws/execution`)
- execution graph events (`/api/v1/ws/execution/events`)

Persistence model:

- canonical multi-tenant state in Neon/Postgres

## 6. Auth and Environment Guardrails

Runtime behavior is environment-sensitive via config:

- `APP_ENV`
- `AUTH_MODE`
- `AUTH_REQUIRED`
- `DATABASE_REQUIRED`

These controls determine whether auth and persistence guardrails are enforced.
