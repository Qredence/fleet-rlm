# Frontend Feature Spec &amp; Information Architecture

> **Audience**: Engineers and AI agents working on the fleet-rlm frontend.
> **Status**: Normative spec for current and near-term surfaces.
> **Companion docs**: [Frontend Product Surface Guide](../guides/frontend-product-surface.md) · [Architecture](../architecture.md) · [Wiring Analysis](../wiring-analysis.md)

---

## Guiding Principle

> **The UI should expose the recursive worker clearly, without exposing the architecture messily.**

Users should feel:

- "I can ask the system to do work"
- "I can see what it's doing"
- "I can inspect memory/artifacts when needed"
- "I can tune how it runs"
- "I can resume or review work later"

Users should **not** feel:

- "I am staring at a transport protocol viewer."

---

## Core User Journeys

| Journey | Primary page | Supporting surfaces |
|---------|-------------|---------------------|
| Start a task | Workbench | Composer, mode selector |
| Monitor progress | Workbench | Execution stream, status badge, inspector |
| Inspect outputs/artifacts | Workbench inspector | Run workbench panel, artifact cards |
| Handle escalation / human review | Workbench | HITL card inline, review panel |
| Revisit prior work | Sidebar session list | Workbench (loaded conversation) |
| Manage volumes/memory | Volumes | Volume browser, file detail panel |
| Adjust execution settings | Settings dialog | Runtime, appearance, optimization sections |
| Optimize prompts | Optimization | GEPA form |

---

## Navigation Model

### Current Navigation

| Item | Route | NavItem | Purpose |
|------|-------|---------|---------|
| **Workbench** | `/app/workspace` | `workspace` | Primary task execution surface |
| **Volumes** | `/app/volumes` | `volumes` | Durable memory/storage browser |
| **Optimization** | `/app/optimization` | `optimization` | GEPA prompt optimization |
| **Settings** | Dialog (or `/app/settings`) | `settings` | Configuration and preferences |

### Session List

The sidebar "Chats" section serves as the session/run history surface. Conversations are stored in `useChatHistoryStore` (localStorage-backed) and sorted by `updatedAt`.

### Future Navigation (out of scope, documented for planning)

| Item | Purpose | Prerequisite |
|------|---------|-------------|
| Runs / History | Dedicated run browser with filters | Backend `/api/v1/sessions` list endpoint |
| Artifacts | Standalone artifact browser | Backend artifact listing endpoint |
| Approvals | Dedicated HITL queue | Backend approval queue endpoint |
| Debug / Traces | Execution trace browser | Backend trace export |

---

## Page Specifications

---

### 1. Workbench Page

**Route**: `/app/workspace`
**Feature module**: `features/workspace/workspace-screen.tsx`
**Purpose**: The place where a user submits a task, sees live execution, reads results, follows recursive progress, and handles escalation.

#### Primary User Actions

| Action | Component | Backend dependency |
|--------|-----------|-------------------|
| Submit a task | `WorkspaceComposer` | `POST /api/v1/ws/execution` (websocket) |
| Monitor live execution | `WorkspaceMessageList` | Streaming WS frames |
| Stop execution | Composer stop button | `stopStreaming()` closes WS |
| Handle HITL checkpoint | HITL card actions | `resolve_hitl` WS command |
| Inspect run details | Inspector side panel | `useRunWorkbenchStore` state |
| Switch execution mode | Composer mode selector | `executionMode` WS param |
| Load prior conversation | Sidebar session click | `useChatHistoryStore` |
| Start new session | Sidebar "New session" | `resetSession()` |

#### Regions

##### A. Header / Context Bar

The global `LayoutHeader` provides:

- Current page identity (Workbench)
- Sidebar toggle
- Canvas panel toggle
- Session status via sidebar navigation

**Should answer**: "Where am I, what context is active?"

##### B. Main Execution Stream

The heart of the page: `WorkspaceMessageList`.

Renders distinct content blocks mapped from `ChatDisplayItem`:

| Block type | `ChatMessage.type` / render part | Visual treatment |
|------------|----------------------------------|-----------------|
| **User message** | `user` | Right-aligned card with task text |
| **Assistant answer** | `assistant` | Left-aligned card with markdown content |
| **Reasoning** | `trace` → `reasoning` part | Collapsible inline reasoning block |
| **Chain of thought** | `trace` → `chain_of_thought` part | Step timeline with status indicators |
| **Tool call** | `trace` → `tool` part | Collapsible tool invocation card |
| **Sandbox execution** | `trace` → `sandbox` part | Code block with output |
| **Status note** | `trace` → `status_note` part | Inline status message |
| **Queue / task progress** | `trace` → `queue` / `task` parts | Checklist progress |
| **HITL checkpoint** | `hitl` | Highlighted review card with action buttons |
| **Clarification request** | `clarification` | Question card with option buttons |
| **Confirmation** | `trace` → `confirmation` part | Approve/reject action card |
| **Error** | WS error frame | Error alert with retry affordance |

Content blocks are grouped into **assistant turns** via `buildChatDisplayItems()`, which merges adjacent trace messages, tool sessions, and reasoning into logical turn units.

**Content tone**: Action-oriented, calm, informative. Show "Running analysis…" not "Dispatching recursive decomposition module".

##### C. Composer / Task Input Area

The `WorkspaceComposer` at the bottom:

- Main text input with contextual placeholder
- Execution mode selector (auto/workspace/etc.)
- Send/stop button with streaming state
- Attachment support (currently text-only)

**Should feel like a task launcher**, not just a chat textbox.

Placeholder text varies by state:

| State | Placeholder |
|-------|------------|
| No backend | "Backend not configured — check Settings → Runtime" |
| Idle, no messages | "Describe what you'd like to build or accomplish…" |
| Idle, has messages | "Continue the conversation or start a new task…" |
| Active | "Ask a follow-up question or provide more context…" |

##### D. Inspector / Context Panel (Canvas)

The collapsible right-side panel (`LayoutSidepanel` → `WorkspaceCanvasPanel`):

| Tab | Content | Component |
|-----|---------|-----------|
| **Execution** | Run status, iterations, callbacks, context sources | `RunWorkbench` |
| **Trajectory** | Turn-level reasoning and step details | `MessageInspectorPanel` |
| **Evidence** | Sources, citations, attachments | Inspector evidence tab |
| **Graph** | Artifact dependency graph | `ArtifactGraph` |

##### E. Terminal Action State

When a run completes, the composer re-enables for follow-up. The run workbench panel shows:

- Final artifact summary
- Iteration history
- Duration and sandbox count
- Error details (if failed)
- Human review details (if escalated)

#### UI States

| State | `RunStatus` | Visual behavior |
|-------|-------------|----------------|
| **Idle** | `idle` | Empty state with suggestions, composer enabled |
| **Starting** | `bootstrapping` | Loading shimmer "Setting up your workspace…", composer disabled |
| **Running** | `running` | Streaming execution blocks, stop button visible |
| **Waiting for review** | `needs_human_review` | HITL card highlighted, action buttons enabled |
| **Completed** | `completed` | Final answer visible, composer re-enabled |
| **Failed** | `error` | Error message with details, composer re-enabled |
| **Cancelled** | `cancelled` | Neutral termination state, composer re-enabled |
| **Cancelling** | `cancelling` | Spinner, "Stopping…" label |

#### Backend Dependencies

| Dependency | Endpoint / mechanism |
|------------|---------------------|
| Execution stream | `/api/v1/ws/execution` (websocket) |
| Execution events | `/api/v1/ws/execution/events` (SSE) |
| Session state | `GET /api/v1/sessions/state` |
| Auth | `GET /api/v1/auth/me` |
| Runtime status | `GET /api/v1/runtime/status` |
| HITL resolution | `resolve_hitl` WS command |
| Trace feedback | `POST /api/v1/traces/feedback` |

#### Out of Scope for Workbench

- Direct Daytona API calls (abstracted by backend)
- Recursive worker decision logic (belongs in `fleet_rlm.worker`)
- Volume management (Volumes page)
- Prompt optimization (Optimization page)

---

### 2. Settings Dialog

**Route**: Dialog overlay (or `/app/settings`)
**Feature module**: `features/settings/settings-screen.tsx`
**Purpose**: Control defaults and user preferences for how the system runs.

#### Sections

| Section | Key | Icon | Purpose |
|---------|-----|------|---------|
| **Appearance** | `appearance` | Paintbrush | Theme, density, scroll behavior |
| **Telemetry** | `telemetry` | Bell | Privacy and analytics preferences |
| **LiteLLM** | `litellm` | Bot | Model provider, endpoint, API key |
| **Runtime** | `runtime` | Cpu | Daytona credentials, connectivity |
| **Optimization** | `optimization` | Sparkles | GEPA configuration and execution |

#### Content Guidance by Section

**Appearance** — UI preferences:

- Theme (light/dark/system)
- Density (compact/comfortable) — future
- Show detailed execution events — future
- Developer mode — future

**Telemetry** — Privacy:

- Analytics opt-in/out
- PostHog integration toggle

**LiteLLM** — Model configuration:

- Provider base URL
- API key
- Planner model selection
- Delegate model selection — future

Copy should explain tradeoffs:

- "Faster models reduce latency but may produce lower-quality reasoning"
- "The planner model handles task decomposition and strategy"

**Runtime** — Sandbox connectivity:

- Daytona API key
- Daytona server URL
- Connection test
- Runtime status display

Copy should clarify what "runtime" means:

- "Connect to a Daytona sandbox for secure isolated code execution"
- "Your code runs in a persistent workspace with durable storage"

**Optimization** — GEPA:

- Metric selection
- Optimization parameters
- Run controls

#### Behavior Principles

- Searchable or clearly sectioned
- Good defaults
- Explanatory copy for each field
- Restore defaults button — future
- Save/cancel semantics
- Avoid exposing raw internal jargon unless in advanced mode

#### Backend Dependencies

| Dependency | Endpoint |
|------------|----------|
| Runtime settings | `GET/PATCH /api/v1/runtime/settings` |
| Runtime status | `GET /api/v1/runtime/status` |
| Optimization | `POST /api/v1/optimization/run`, `GET /api/v1/optimization/status` |

---

### 3. Volumes Page

**Route**: `/app/volumes`
**Feature module**: `features/volumes/volumes-screen.tsx`
**Purpose**: Let users understand and manage durable workspace/memory containers.

Volumes are not just storage — they are **part of the durable memory model**. The UI should help users understand: "What durable information does this agent retain here?"

#### Primary User Actions

| Action | Component | Backend dependency |
|--------|-----------|-------------------|
| Browse volume tree | `VolumesBrowser` | `GET /api/v1/runtime/volumes` |
| Search files | Search input | Client-side `filterFs()` |
| Inspect file content | Canvas detail panel | Client-side from volume tree |
| Expand/collapse tree | Expand/Collapse buttons | Client-side state |
| Refresh | Refresh button | Re-fetch volume tree |

#### Volume Tree Structure

The volume browser renders a hierarchical tree of `FsNode` items:

| Node type | Visual treatment |
|-----------|-----------------|
| Volume root | `HardDrive` icon, label style, file count badge |
| Directory | Folder/FolderOpen icon, expandable |
| File | Type-specific icon (`.py`, `.md`, `.json`, etc.), size display |

Durable mounted-volume roots follow the pattern: `memory/`, `artifacts/`, `buffers/`, `meta/`.

#### UI States

| State | Visual behavior |
|-------|----------------|
| Loading | Centered spinner, "Loading durable volume tree…" |
| Empty | "No files found in the durable volume." |
| Degraded | Warning alert explaining volume endpoint is unavailable |
| Populated | Tree view with expand/collapse, search, file count |

#### File Detail Panel (Canvas)

When a file is selected, the canvas panel opens with:

- File name and path
- File size and modification date
- Content preview (for supported types)

#### Content Guidance

Copy should explain persistence:

- "Browse the Daytona mounted durable volume for this workspace"
- "Files here persist across sessions and execution runs"
- What is stored (memory files, artifacts, patches, generated outputs, logs, session metadata)
- What is safe to remove
- What is reused across runs

#### Volume Safety

Because this page touches durable state, future destructive actions should:

- Be explicit with confirmation dialogs
- Show impact clearly
- Support archive before delete if possible

#### Backend Dependencies

| Dependency | Endpoint |
|------------|----------|
| Volume tree | `GET /api/v1/runtime/volumes` (or Daytona direct) |
| File content | Volume content endpoints |
| Runtime status | `GET /api/v1/runtime/status` |

#### Out of Scope

- Volume creation/deletion (future)
- Volume import/export (future)
- Memory/knowledge graph view (future)

---

### 4. Optimization Page

**Route**: `/app/optimization`
**Feature module**: `features/optimization/optimization-screen.tsx`
**Purpose**: Configure and run GEPA prompt optimization.

#### Primary User Actions

| Action | Component | Backend dependency |
|--------|-----------|-------------------|
| Configure optimization | `OptimizationForm` | `POST /api/v1/optimization/run` |
| Check status | Status display | `GET /api/v1/optimization/status` |

#### Backend Dependencies

| Dependency | Endpoint |
|------------|----------|
| Run optimization | `POST /api/v1/optimization/run` |
| Check status | `GET /api/v1/optimization/status` |

---

### 5. Session List (Sidebar)

**Location**: `LayoutSidebar` → "Chats" group
**Purpose**: Review, resume, and inspect past work.

#### Current State

- Conversations stored in `useChatHistoryStore` (localStorage)
- Sorted by `updatedAt` descending
- Title derived from first user message
- Delete action per conversation
- Load conversation restores messages, phase, and turn artifacts

#### Content

Each session entry shows:

- Title (truncated)
- Delete action (hover reveal)

#### Future Enhancements (documented for planning)

A dedicated Runs/History page would add:

- Run/session list with status badges
- Task summary
- Workspace/volume used
- Key outputs
- Escalation status
- Filters: running, completed, failed, needs review, by workspace, by volume

---

## Normalized UI State Model

All pages share a consistent execution state model via `RunStatus`:

```typescript
type RunStatus =
  | "idle"           // Ready for input
  | "bootstrapping"  // Sandbox starting
  | "running"        // Execution in progress
  | "completed"      // Finished successfully
  | "error"          // Execution failed
  | "needs_human_review"  // HITL checkpoint
  | "cancelled"      // User stopped execution
  | "cancelling";    // Stop in progress
```

This prevents each page from inventing its own interpretation. Status is managed by `useRunWorkbenchStore` and displayed via shared `StatusBadge` and `StatusIndicator` components.

---

## Shared Component System

### Existing Pattern Components

| Component | Module | Purpose |
|-----------|--------|---------|
| `StatusBadge` | `components/patterns/execution-status.tsx` | Colored status pill with icon |
| `StatusIndicator` | `components/patterns/execution-status.tsx` | Small status dot |
| `StatusMessage` | `components/patterns/execution-status.tsx` | Status alert box |
| `ExecutionProgress` | `components/patterns/execution-status.tsx` | Progress bar |
| `Section` | `components/patterns/section-layout.tsx` | Consistent section wrapper |
| `SectionHeader` | `components/patterns/section-layout.tsx` | Section title + action |
| `SectionCard` | `components/patterns/section-layout.tsx` | Bordered content card |
| `EmptyPanel` | `components/patterns/empty-panel.tsx` | Empty state display |

### Existing AI Element Components

| Component | Module | Purpose |
|-----------|--------|---------|
| `Conversation` | `components/ai-elements/conversation` | Chat container |
| `Message` | `components/ai-elements/message` | Individual message block |
| `Reasoning` | `components/ai-elements/reasoning` | Collapsible reasoning |
| `Suggestion` | `components/ai-elements/suggestion` | Quick-action chip |
| `Tool` | `components/ai-elements/tool` | Tool call display |
| `Sources` | `components/ai-elements/sources` | Source list |
| `Task` | `components/ai-elements/task` | Task checklist |
| `InlineCitation` | `components/ai-elements/inline-citation` | Citation card |

### Workspace-Specific Components

| Component | Module | Purpose |
|-----------|--------|---------|
| `WorkspaceComposer` | `app/workspace/workspace-composer.tsx` | Task input area |
| `WorkspaceMessageList` | `app/workspace/transcript/workspace-message-list.tsx` | Execution stream |
| `RunWorkbench` | `app/workspace/workbench/run-workbench.tsx` | Execution detail panel |
| `MessageInspectorPanel` | `app/workspace/inspector/message-inspector-panel.tsx` | Turn inspector |
| `ArtifactGraph` | `app/workspace/inspector/artifact-graph.tsx` | Step dependency graph |
| `ClarificationCard` | `app/workspace/clarification-card.tsx` | Clarification prompt |
| `ChainOfThought` | `app/workspace/chain-of-thought.tsx` | Step timeline |
| `Sandbox` | `app/workspace/sandbox.tsx` | Code execution display |

### Recommended Future Components

| Component | Purpose |
|-----------|---------|
| `RunHeader` | Compact run metadata bar (status, duration, sandbox info) |
| `ResultCard` | Terminal answer card with copy/export actions |
| `ArtifactList` | Browsable list of produced artifacts |
| `ReviewRequiredCard` | Explicit HITL card with reason and actions |
| `SessionListItem` | Richer session entry with status badge and metadata |
| `VolumeCard` | Volume summary card with storage stats |

---

## Content & Copy Guidance

### Workbench Copy Style

Should feel action-oriented, calm, informative:

| ✅ Good | ❌ Avoid |
|---------|----------|
| "Running analysis in workspace…" | "Dispatching recursive decomposition module" |
| "Review needed before continuing" | "HITL checkpoint reached at depth 3" |
| "Repair attempt completed" | "ReAct loop iteration 4 finalized" |
| "Final result ready" | "Terminal frame received, run_status=completed" |
| "Setting up your workspace…" | "Bootstrapping Daytona sandbox instance" |

### Settings Copy Style

Should explain tradeoffs:

- Speed vs depth: "Faster models reduce latency but may produce lower-quality reasoning"
- Autonomy vs approval: "Require confirmation before modifying files"
- Concise vs detailed: "Show detailed execution events in the timeline"

### Volume Copy Style

Should explain persistence:

- "Files here persist across sessions and execution runs"
- "The memory directory stores learned context for future tasks"
- "Artifacts contain outputs generated during execution"

---

## State Management Architecture

### Store Boundaries

```
┌─────────────────────────────────────────────────┐
│ Global Stores                                    │
│  useNavigationStore    — shell nav + canvas       │
│  useThemeStore         — theme preference          │
│  useTokenStore         — auth tokens               │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Workspace Stores                                 │
│  useChatStore          — messages, streaming       │
│  useWorkspaceUiStore   — inspector, selection      │
│  useRunWorkbenchStore  — execution state            │
│  useArtifactStore      — execution artifacts        │
│  useChatHistoryStore   — persisted conversations   │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Volumes Store                                    │
│  useVolumesSelectionStore — selected file state    │
└─────────────────────────────────────────────────┘
```

### State Flow: WebSocket → UI

```
Backend Worker (fleet_rlm.worker)
    ↓ produces events
Agent Host (fleet_rlm.agent_host)
    ↓ streams via websocket
FastAPI WS Endpoint (/api/v1/ws/execution)
    ↓
Frontend WS Client (lib/rlm-api/ws-client.ts)
    ↓
Frame Parser (lib/rlm-api/ws-frame-parser.ts)
    ↓
Chat Event Adapter (lib/workspace/backend-chat-event-adapter.ts)
    ↓ maps to ChatMessage[]
Chat Store (lib/workspace/chat-store.ts)
    ↓
Display Item Builder (lib/workspace/chat-display-items.ts)
    ↓ groups into AssistantTurnDisplayItem[]
Workspace Message List (app/workspace/transcript/)
    ↓ renders
Trace Part Renderers (app/workspace/transcript/trace-part-renderers.tsx)
```

---

## Backend Boundary Rules

The frontend must **not**:

1. Duplicate recursive worker decision logic
2. Re-derive backend semantics from ad hoc heuristics
3. Make direct Daytona API calls
4. Store sensitive credentials
5. Let styling work drive protocol churn

The frontend **should**:

1. Consume the backend contract cleanly
2. Map backend states to UI states using the normalized `RunStatus` model
3. Render backend events through the adapter → store → renderer pipeline
4. Keep transport concerns in `lib/rlm-api/`
5. Keep state management in `lib/workspace/`
6. Keep rendering in `app/workspace/`

---

## Recommended Implementation Priorities

### Must-Have (current phase)

- [x] Workbench page polished and correctly wired
- [x] Settings dialog structured by section
- [x] Volume page browsable and understandable
- [x] Session history usable via sidebar
- [x] Human review surfaced clearly via HITL cards
- [x] Shared pattern components (`StatusBadge`, `Section`, `EmptyPanel`)
- [x] Frontend architecture guide

### Next Phase

- [ ] Richer session list with status badges and metadata
- [ ] Terminal action buttons (continue, refine, repair, export)
- [ ] Run header component with execution summary
- [ ] Result card with copy/export actions
- [ ] Artifact list component
- [ ] Settings "Advanced" mode toggle
- [ ] Volume detail panel improvements (memory organization view)
- [ ] Dedicated Runs/History page

### Future

- [ ] Artifacts page or drawer
- [ ] Trace/debug page
- [ ] Multi-pane power-user layout
- [ ] Advanced preferences (recursion depth, repair limits, verification toggles)
- [ ] Approval queue page
- [ ] Admin/debug dashboard
