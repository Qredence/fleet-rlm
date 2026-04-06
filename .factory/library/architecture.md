# Architecture

Backend architecture for the `src/fleet_rlm` simplification mission.

**What belongs here:** high-level ownership boundaries, runtime flow, and invariants workers should preserve.
**What does NOT belong here:** per-feature implementation notes or temporary TODOs.

---

## Product Identity

`fleet-rlm` is a Web UI-first FastAPI backend for a long-running RLM runtime.

The backend is responsible for serving and coordinating all of these surfaces from one product-shaped application:
- browser shell served by FastAPI
- canonical HTTP contract under `/api/v1`
- websocket chat stream under `/api/v1/ws/chat`
- websocket execution stream under `/api/v1/ws/execution`
- CLI entrypoints that launch or exercise the same backend/runtime system
- Daytona-backed workspace/runtime infrastructure

This mission should make the code read more like that product reality.

## Current High-Level Layers

### `api/`
Owns transport-facing behavior:
- FastAPI app factory and lifespan
- route registration and middleware
- auth and request/websocket identity resolution
- websocket orchestration, event shaping, and execution stream subscription
- runtime-facing HTTP services (`runtime_services/*`)

### `runtime/`
Owns shared conversational/runtime behavior:
- shared ReAct chat agent and session logic
- streaming helpers and execution interpreter contracts
- runtime tools and long-context execution utilities
- shared runtime models/signatures/content processing

### `integrations/`
Owns infrastructure and provider-specific behavior:
- environment/config loaders
- database/repository layer
- observability integrations
- provider-specific runtime implementations, especially Daytona

### `cli/`
Owns command-line entry plumbing only:
- `fleet` launcher surface
- `fleet-rlm` Typer commands
- CLI config bootstrap and terminal chat UX

Target direction: CLI stays thin; backend/runtime ownership becomes explicit elsewhere.

## Target Ownership Direction

### Backend-owned runtime assembly
The FastAPI/backend path should assemble runtime-mode chat agents through backend-owned seams, even if CLI entrypoints reuse the same underlying helpers. Workers should prefer a backend-facing factory/assembly module over having `api` depend on CLI-oriented runners.

### Websocket/session lifecycle
The websocket package should make these phases obvious from names and import direction:
1. authenticate and resolve identity
2. prepare runtime models/state
3. build agent context for the requested runtime mode
4. drive streaming lifecycle
5. persist session/manifests/execution state
6. shape completion/failure payloads

Workers may merge or move modules if it improves discoverability, but must preserve this lifecycle contract.

### Daytona as first-class infrastructure
Daytona is not a side path. Provider-specific workspace/interpreter/runtime logic belongs under `integrations/providers/daytona/*`, but it must continue to plug into the shared conversational/runtime contract rather than forking a separate chat architecture.

## Invariants

- One FastAPI app instance still serves health/readiness, HTTP routes, websocket routes, and the browser shell.
- `fleet web` remains a thin path to the same backend app.
- `/api/v1/ws/chat` stays the canonical conversational stream.
- `/api/v1/ws/execution` stays the canonical execution/workbench stream.
- `runtime_mode=daytona_pilot` continues to use the shared websocket/chat contract with Daytona-specific execution underneath it.
- Session state remains identity-scoped.
- Route/CLI/browser contract behavior must be preserved unless the mission explicitly records an intentional cleanup decision.

## Highest-Risk Seams

- API code that still constructs runtime objects through CLI-owned helpers
- websocket runtime preparation versus stream/lifecycle ownership split across many files
- Daytona wrappers that obscure whether behavior is shared-runtime or provider-specific
- package-root facades and compatibility exports that hide the real owner modules

Workers should collect reachability evidence before collapsing any of these seams.
