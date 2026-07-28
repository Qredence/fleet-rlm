# Fleet RLM Architecture Visualizations

This directory contains Graphviz DOT source files for Fleet RLM architecture documentation. These visualizations are machine-readable sources that can be rendered into SVG, PNG, or viewed in Qoder's built-in DOT viewer.

## Files

### 1. `FleetRLM_module_dependencies.dot`

**Purpose:** Module dependency graph showing relationships between subsystem packages.

**What it shows:**
- All major subsystems (api/, chat/, rlm/, daytona/, files/, skills/, persistence/, etc.)
- Internal module dependencies within each subsystem
- Cross-package dependencies showing key data flows
- Color-coded by subsystem for easy identification

**Key insights:**
- Tight coupling within subsystems vs. loose coupling between them
- Key integration points (chat/ orchestrates everything)
- Daytona SDK boundary (only imported in daytona/)
- Skill system as standalone catalog

**How to read:**
- Boxes = Python modules (.py files)
- Arrows = dependency direction (A → B means A uses B)
- Colors = logical grouping (same subsystem)
- Labels show class names/functions implemented

**Usage scenarios:**
- Understanding impact of changes
- Finding where to add new features
- Identifying coupling issues
- Onboarding new developers

---

### 2. `FleetRLM_turn_execution.dot`

**Purpose:** Complete Turn execution lifecycle flow from HTTP request to completion.

**What it shows:**
- Main flow: HTTP entry → validation → orchestration → execution → persistence → response
- Event streaming path (RuntimeEvents → AI SDK UI v1 SSE chunks)
- Daytona interpreter lease acquisition and release
- Atomic commit pattern at finish()
- Parallel operations (MLflow tracing, sandbox monitoring)

**Key phases:**
1. **HTTP Layer**: Request validation, error handling
2. **Orchestration**: coordinator.open() → prepare → execute → finish → cleanup
3. **Runtime**: Fresh dspy.RLM instance, recursive execution loop
4. **Sandbox**: Interpreter lease, code execution via REPL + HTTP broker
5. **Persistence**: TurnClaim transitions, artifact promotion, atomic commit
6. **Events**: SSE projection, AI SDK UI v1 protocol

**Key metrics:**
- Max 8 concurrent interpreter leases (admission control)
- 5-minute idle timeout before sandbox stop
- Recursive RLM loop bounded by max_iterations

**Usage scenarios:**
- Debugging Turn execution failures
- Understanding SSE event ordering
- Planning Turn optimization
- Explaining atomic commit guarantees

---

### 3. `FleetRLM_daytona_sandbox_lifecycle.dot`

**Purpose:** State machine for Daytona sandbox lifecycle and interpreter lease management.

**What it shows:**
- Sandbox states: Created → Started → Running → Interpreting → Idle → Stopped
- Admission control with semaphore-based concurrency limiting
- Provisioning phase: snapshot loading, volume creation, workspace scoping
- Lease management: acquire interpreter, execute turn, release permit
- Idle timeout policy: 5-minute grace period

**State transition rules:**
- Created → Started: Must call provisioner.start()
- Started → Running: Passes ready check
- Running → Interpreting: Lease acquired (semaphore permit)
- Interpreting → Idle: Interpreter released (lease retained)
- Idle → Stopped: After 5-minute timeout OR manual stop()
- Stopped → Deleted: Explicit delete call (volume persists)

**Key constraints:**
- MAX 8 concurrent leases enforced by asyncio.BoundedSemaphore
- Volume identity persists across sandbox replacement
- HTTP broker mediates all host-tool calls and SUBMIT capture
- Released sandboxes stopped after explicit idle timeout

**Resource limits (per sandbox):**
- 1 CPU
- 1 GiB RAM
- 3 GiB disk

**Usage scenarios:**
- Understanding sandbox provisioning costs
- Diagnosing lease exhaustion
- Planning scaling strategy
- Optimizing idle time behavior

---

## How to View & Render

### Using Qoder's Built-in Viewer

If you're using Qoder IDE:
1. Open any `.dot` file
2. The DOT preview panel should auto-render the visualization
3. If not, click the "View Visualization" button in the editor toolbar

### Command Line Rendering

Install Graphviz and use:

```bash
# Install Graphviz (macOS)
brew install graphviz

# Convert DOT to SVG
dot -Tsvg FleetRLM_module_dependencies.dot > FleetRLM_module_dependencies.svg
dot -Tsvg FleetRLM_turn_execution.dot > FleetRLM_turn_execution.svg
dot -Tsvg FleetRLM_daytona_sandbox_lifecycle.dot > FleetRLM_daytona_sandbox_lifecycle.svg

# Convert DOT to PNG
dot -Tpng FleetRLM_module_dependencies.dot > FleetRLM_module_dependencies.png

# Generate all formats at once
for dot in *.dot; do
    dot -Tsvg "$dot" > "${dot%.dot}.svg"
    dot -Tpng "$dot" > "${dot%.dot}.png"
done
```

### Online Renderers

Use online tools like:
- https://dreampuf.github.io/GraphvizOnline
- https://mermaid.live (supports some DOT)
- VS Code extension "Dot Preview"

---

## Reading Order Recommendation

For new team members or deep understanding:

1. **Start with** `FleetRLM_turn_execution.dot`
   - Understand the main workflow first
   - See how components interact end-to-end
   
2. **Then study** `FleetRLM_module_dependencies.dot`
   - Map execution flow to actual code structure
   - Identify which files implement each component
   
3. **Finally examine** `FleetRLM_daytona_sandbox_lifecycle.dot`
   - Deep dive into Daytona-specific details
   - Understand resource management and timeouts

---

## Key Design Principles Illustrated

### 1. Fresh RLM Per Turn
The turn execution diagram shows every Turn gets a new `dspy.RLM` instance. No state leaks between Turns.

### 2. Atomic Commit Pattern
`TurnLifecycle.finish()` owns result validation, private snapshots, artifact promotion, and atomic database commits together. Either all succeeds or none happens.

### 3. Admission Control
Max 8 concurrent interpreter leases prevent Daytona API overload. Semaphore-based fairness ensures no starvation.

### 4. Fail-Soft Observability
MLflow tracing runs in parallel but never affects Turn outcomes. If tracing fails, Turn continues normally.

### 5. Transport-Neutral Events
RuntimeEvents are typed domain objects only projected to SSE in `api/sse.py`. This keeps core logic transport-independent.

### 6. Volume-Persistent Identity
Daytona volumes persist across sandbox lifecycle. Workspace mounting is fast on subsequent Runs because volume already exists.

---

## Evidence Sources

All visualizations are grounded in actual source code:

| Diagram | Evidence Sources |
|---------|------------------|
| Module Dependencies | `src/fleet_rlm/**/*.py` imports, class instantiations |
| Turn Execution | `chat/turn_coordinator.py`, `rlm/runner.py`, `daytona/session_manager.py`, `persistence/repositories/turns.py` |
| Sandbox Lifecycle | `daytona/provisioning.py`, `daytona/session_manager.py`, `composition/daytona.py` |

**Generated from:** Knowledge extracted from Fleet RLM knowledge base  
**Last updated:** 2026-07-28  
**Validation:** Verified against source code import graphs and runtime traces

---

## Related Documentation

- [QoderWiki System](/QoderWiki) — Knowledge base overview
- [Fleet RLM Backend](/FleetRLMBackend) — Backend subsystem deep dive
- [Fleet Terminal Client](/FleetTerminalClient) — TUI application docs
- [System Architecture](../overview/architecture.md) — High-level architecture
- [Codebase Map](../docs/reference/codebase-map.md) — Module ownership mapping

---

## Maintenance Guidelines

### Updating Visualizations

When making significant changes:
1. **Module additions/deletions**: Update `FleetRLM_module_dependencies.dot`
2. **Workflow changes**: Update `FleetRLM_turn_execution.dot`
3. **Resource policies**: Update `FleetRLM_daytona_sandbox_lifecycle.dot`

### Version Tracking

Add comments to DOT files indicating:
- What changed (new feature, bug fix, refactor)
- Date of change
- Related issue/PR numbers

### Validation

After editing:
```bash
# Check DOT syntax
dot -Tnull your_file.dot  # Should output nothing if valid

# Re-render to verify
dot -Tsvg your_file.dot > /tmp/test.svg
open /tmp/test.svg  # Quick visual check
```

---

## Export Formats

These DOT sources support multiple output formats:

- **SVG**: Best for web embedding, infinite zoom without quality loss
- **PNG**: Good for presentations, screenshots, documentation images
- **PDF**: Print-ready vector graphics
- **Mermaid-compatible**: Some tools can convert DOT → Mermaid
- **PlantUML**: Alternative UML tool with DOT-like syntax

**Recommendation**: Keep DOT as canonical source; generate other formats as needed for specific audiences.

---

*This documentation is maintained as part of the QoderWiki knowledge system.*

