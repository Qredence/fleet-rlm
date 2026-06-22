# Frontend Feature Spec

> Audience: engineers and AI agents working on the Fleet RLM frontend.
> Status: normative spec for the current frontend surfaces.

## Guiding Principle

The UI should expose the recursive worker clearly without exposing the
transport or layout machinery.

Users should feel that they can:

- submit a task
- watch the system work
- inspect outputs and artifacts
- tune execution settings
- resume prior work from the workbench session list

Users should not feel that they are staring at a transport protocol viewer or a
retired screen shell.

## Product Surfaces

| Route | Surface | Owning feature module | Purpose |
| --- | --- | --- | --- |
| `/app/workspace` | Workbench | `features/workspace/screen/*` | Primary chat execution surface with workspace-local sidepanel |
| `/app/optimization` | Optimization | `features/optimization/*` | GEPA optimization form, run history, and run inspection |
| `/app/volumes` | Volumes | `features/volumes/*` | Full-page mounted Daytona volume browser |
| `/app/settings` | Settings | `features/settings/settings-screen.tsx` | Runtime and app settings |

## Workbench Spec

The workbench is the primary live surface. It is chat-first, but it is not just
a chat box.

### Ownership

- `WorkspaceScreen` owns runtime bootstrap, placeholder behavior, session
  persistence, and the execution mode selector.
- `useWorkspace()` owns streaming, cancel, HITL resolution, and conversation
  loading.
- `useChatStore` owns live transcript state.
- `useRunWorkbenchStore` owns execution summaries, artifacts, iterations,
  callbacks, and completion metadata.
- `workspace-ui-store` owns workspace-local sidepanel state, including
  collapsed/resized layout and active tab.

### Regions

| Region | Purpose | Main modules |
| --- | --- | --- |
| Header | Page identity, sidebar toggle, workspace sidepanel toggle | `features/layout/header.tsx` |
| Transcript | Live user/assistant/trace rendering | `features/workspace/conversation/*` |
| Composer | Submit, stop, execution mode, attachments | `features/workspace/conversation/*`, `components/agent-elements/input-bar.tsx` |
| Sidepanel | Trajectories, graph, and inline volume browsing | `features/workspace/screen/*`, `features/workspace/workbench/*`, `lib/workspace/*` |
| HITL | Human review / approval | `features/workspace/conversation/*` and `features/workspace/inspection/*` |
| Session sidebar | Local conversation history and session actions | `features/workspace/session/*` |

### Workbench States

| State | Behavior |
| --- | --- |
| `idle` | Composer enabled, empty state visible |
| `understanding` | Request is being prepared |
| `running` | Live transcript and workbench updates stream in |
| `needs_human_review` | HITL card blocks continuation until resolved |
| `complete` | Final answer and summary are visible |
| `error` | Failure state with retry affordance |
| `cancelled` | Neutral termination state |

### Transcript Model

The transcript is built from normalized backend frames and grouped into
assistant turns.

Key render categories:

- user message
- assistant answer
- reasoning
- trajectory
- tool call and tool result
- sandbox output
- status note
- HITL request / resolution
- clarification request
- plan update / RLM execution / memory update

The transcript is summary-friendly, but the canonical completion state belongs
in the workbench panel.

### Workspace Sidepanel

Workspace chat is primary. The sidepanel is local to Workbench, collapsible,
and resizable; it should not be modeled as the global shell canvas.

Supported tabs are exactly:

- `Trajectories`
- `Graph`
- `Volume`

`Trajectories` and `Graph` resolve traces by durable chat session id first and
runtime `external_session_id` when available. If MLflow traces are unavailable,
they fall back to live transcript rows and artifact summary data.

`Volume` uses Daytona volume APIs, includes inline preview, and supports a
resizable tree/preview split.

## Optimization Spec

The Optimization surface configures GEPA optimization runs and inspects their
results. It is a routed product surface, not a settings-only form.

Rules:

- `features/optimization/screen/optimization-screen.tsx` owns the page shell.
- `features/optimization/form/*` owns target, dataset, reflection, and advanced
  run configuration.
- `features/optimization/run-history/*` and `run-details/*` own run browsing
  and detailed inspection.
- `lib/rlm-api/optimization.ts` is the typed client boundary for
  `/api/v1/optimization/*`.

## Volumes Spec

The Volumes surface is the full-page browser for the mounted Daytona volume
tree. It remains distinct from the workspace sidepanel `Volume` tab.

Rules:

- selecting a file opens the full-page preview region
- leaving the Volumes route clears the selected file
- the volume tree should be treated as mounted durable storage, not the live
  workspace session

## Settings Spec

Settings can open as a dialog or as the routed fallback page.

Supported sections:

- `appearance`
- `telemetry`
- `litellm`
- `runtime`

Rules:

- the dialog is the primary entrypoint
- the routed page is a fallback and compatibility surface
- runtime settings and connectivity checks live in the settings feature tree
- optimization settings reuse the optimization form from the optimization
  feature

## Navigation And Shell Rules

- `RootLayout` owns the shell chrome.
- `RouteSync` keeps the URL and navigation store aligned.
- `NavigationStore` is the client-side shell state for active navigation.
- `layout` owns the shell UX; product surfaces own their own internal logic.
- route files stay thin and should not acquire page-level business logic.
