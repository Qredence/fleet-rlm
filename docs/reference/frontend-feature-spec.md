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
- revisit prior work

Users should not feel that they are staring at a transport protocol viewer or a
legacy screen shell.

## Product Surfaces

| Route | Surface | Owning feature module | Purpose |
| --- | --- | --- | --- |
| `/app/workspace` | Workbench | `features/workspace/workspace-screen.tsx` | Primary task execution surface |
| `/app/volumes` | Volumes | `features/volumes/volumes-screen.tsx` | Mounted Daytona volume browser |
| `/app/history` | History | `features/history/history-screen.tsx` | Session and conversation history |
| `/app/optimization` | Optimization | `features/optimization/optimization-screen.tsx` | GEPA prompt optimization |
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

### Regions

| Region | Purpose | Main modules |
| --- | --- | --- |
| Header | Page identity, sidebar toggle, canvas toggle | `features/layout/header.tsx` |
| Transcript | Live user/assistant/trace rendering | `features/workspace/ui/transcript/*` |
| Composer | Submit, stop, execution mode, attachments | `features/workspace/ui/workspace-composer.tsx` |
| Canvas / Inspector | Turn details, graph, execution panel, run panel | `features/workspace/workspace-canvas-panel.tsx` and `features/workspace/ui/*` |
| HITL | Human review / approval | `features/workspace/ui/hitl-approval-modal.tsx` |
| Session sidebar | Local conversation history and session actions | `features/workspace/ui/session-sidebar.tsx` |

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

## Volumes Spec

The Volumes surface is a browser for the mounted Daytona volume tree.

Rules:

- selecting a file opens the shell canvas
- leaving the Volumes route clears the selected file
- the canvas is a preview/detail area, not the primary browser
- the volume tree should be treated as mounted durable storage, not the live
  workspace session

## History Spec

History is a first-class routed surface, not a sidebar-only concept.

Rules:

- it lists backend sessions and local conversations
- it can open a detail drawer without leaving the shell
- it supports replay/inspection of prior work
- it is separate from the live workbench turn flow

## Optimization Spec

The optimization surface is a standalone product area.

Rules:

- it exposes modules, datasets, runs, and compare tabs
- it does not participate in the live workspace websocket turn
- it should reuse shared optimization form logic rather than duplicating config

## Settings Spec

Settings can open as a dialog or as the routed fallback page.

Supported sections:

- `appearance`
- `telemetry`
- `litellm`
- `runtime`
- `optimization`

Rules:

- the dialog is the primary entrypoint
- the routed page is a fallback and compatibility surface
- runtime settings and connectivity checks live in the settings feature tree
- optimization settings reuse the optimization form from the optimization
  feature

## Navigation And Shell Rules

- `RootLayout` owns the shell chrome.
- `RouteSync` keeps the URL and navigation store aligned.
- `NavigationStore` is the client-side shell state for active nav and canvas
  visibility.
- `layout` owns the shell UX; product surfaces own their own internal logic.
- route files stay thin and should not acquire page-level business logic.

