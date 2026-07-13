# Clean package map

Paths relative to `src/fleet_rlm/`.

| Path | Owns |
|------|------|
| `api/routes/chat.py` | `POST /api/chat` SSE entry |
| `api/routes/skills.py` | SkillCards list/get (no bodies) |
| `api/routes/files.py` | Attachment upload |
| `api/routes/artifacts.py` | Artifact CRUD |
| `api/routes/runs.py` | Run cancel / status |
| `chat/turn_coordinator.py` | Isolation, lease acquire, turn assembly |
| `rlm/runner.py` | `dspy.RLM` execute + event projection |
| `rlm/factory.py` | RLM construction (`max_iterations`, tools) |
| `rlm/signature.py` | `FleetRLMSignature` |
| `rlm/budgets.py` | Finite turn budgets |
| `rlm/context.py` | `RLMTurnContext` |
| `daytona/session_manager.py` | Sandbox acquire / volume mount |
| `daytona/interpreter.py` | `code_interpreter` adapter |
| `daytona/paths.py` | Volume path layout under mount root |
| `skills/registry.py` | Host skill records |
| `skills/tools.py` | Progressive `load_skill` host tools |
| `skills/loader.py` | Seed registry from bundled `skills/skills/` |
| `files/tools.py` | Attachment / artifact host tools |
| `observability/` | TurnTrace + exporters |

Live package `src/fleet_rlm/` (WebSocket, EscalatingFleetModule, GEPA CLI) is **out of scope** for these skills.
