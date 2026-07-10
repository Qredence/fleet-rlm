# Frontend experience

The frontend context names the user-facing streaming and interactive surfaces of Fleet RLM.

## Language

**Transcript Stream**:
The ordered user-facing chat output for one turn, delivered through the canonical SSE chat contract.
_Avoid_: runtime event stream, control socket

**Control Channel**:
A genuinely bidirectional interface for terminal, sandbox input, cancellation, resize, or comparable interactive control.
_Avoid_: primary chat transport, transcript stream

**Projected Part**:
A frontend-safe transcript unit classified and emitted by the backend for rendering.
_Avoid_: raw runtime event, provider payload
