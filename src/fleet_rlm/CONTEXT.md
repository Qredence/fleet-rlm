# Backend runtime

The backend runtime context names the execution roles and migration boundaries of Fleet RLM.

## Language

**RLM-native Backend**:
A backend whose primary engine for open-ended agentic work is `dspy.RLM`, while bounded DSPy modules and deterministic Python remain responsible for narrow prediction and policy work.
_Avoid_: RLM-only backend, legacy runtime with an RLM feature

**Execution Backend**:
The server-selected runtime implementation that executes a turn. It is distinct from client-visible turn controls and behavior modes.
_Avoid_: execution mode, client-selected backend

**Compatibility Runtime**:
The legacy agent runtime retained temporarily for migration and rollback while the RLM-native path earns and sustains promotion evidence.
_Avoid_: primary runtime, permanent duplicate implementation

**Runtime Event**:
The backend-neutral record of turn progress and completion consumed by observability and transport projection.
_Avoid_: SSE part, WebSocket frame
