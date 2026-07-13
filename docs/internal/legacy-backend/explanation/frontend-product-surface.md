# Frontend Product Surface Guide

This guide documents the current product surfaces in the Fleet RLM frontend.
It is intentionally aligned with the live `features/*` ownership model and the
Daytona-backed runtime contract.

## Product Flow Overview

The supported product flow is a chat-first execution workbench:

1. The user submits a task in Workbench.
2. The frontend creates or reuses a session id and opens `/api/v1/ws/execution`.
3. The backend streams live reasoning, tool, status, and final frames.
4. The transcript updates in real time.
5. The workspace sidepanel hydrates trajectories, graph, and volume state from
   execution summary, final artifacts, live transcript data, and Daytona volume
   APIs.
6. The user can also open optimization, full-page volumes, or settings from the
   same shell.

## Surface Map

| Surface | Route | Owns | Notes |
| --- | --- | --- | --- |
| Workbench | `/app/workspace` | `features/workspace/*` | Primary chat execution surface with workspace-local sidepanel |
| Optimization | `/app/optimization` | `features/optimization/*` | GEPA optimization setup, run history, and run inspection |
| Volumes | `/app/volumes` | `features/volumes/*` | Full-page mounted Daytona volume browser |
| Settings | `/app/settings` | `features/settings/*` | Dialog-first settings surface |

## Layer Structure

```text
src/frontend/src/
├── routes/                # Thin TanStack Router wrappers
├── features/
│   ├── layout/            # Shell chrome, route sync, dialogs, sidebar, header
│   ├── workspace/         # Workbench chat, sidepanel, transcript, session controls
│   ├── optimization/      # GEPA optimization form, run history, and details
│   ├── volumes/           # Full-page volume browser and file preview
│   └── settings/          # Settings dialog and runtime forms
├── lib/
│   ├── workspace/         # Zustand stores, event adapters, hydration reducers
│   └── rlm-api/           # REST and websocket clients
├── stores/                # Shell/navigation state
├── components/ui/         # Shared UI primitives
├── components/agent-elements/ # Agent Elements rendering primitives
└── components/product/    # Reusable product composition
```

## Workbench Behavior

The workbench is the only live execution surface.

Rules:

- `WorkspaceScreen` initializes `daytona_pilot` as the runtime mode.
- `useWorkspace()` submits messages, streams websocket frames, handles HITL,
  and manages local conversation loading.
- `useChatStore` holds the active session id and streaming transcript.
- `useRunWorkbenchStore` holds the execution panel state.
- The workbench panel must hydrate from `execution_completed.summary` and
  `final_artifact`, not from transcript scraping.
- The passive execution stream exists only for execution summary and workbench
  hydration.

## Workspace Sidepanel Behavior

Workspace chat remains the primary surface. The sidepanel belongs to the
workspace feature, not to the global shell, and it is collapsible and
resizable.

Supported tabs are exactly:

- `Trajectories`
- `Graph`
- `Volume`

`Trajectories` and `Graph` resolve the active run by durable chat session id or
runtime `external_session_id`. If MLflow traces cannot be loaded, they fall
back to the live transcript and artifact data already streamed into workspace
state.

`Volume` browses Daytona volume data inline with a resizable tree/preview split.
The `/app/volumes` route remains the full-page durable volume browser.

## What Is Out Of Scope

The current frontend does not treat these as product surfaces:

- `taxonomy`
- `skills`
- `memory`
- `analytics`
- `history`

New work should target `features/*`, `lib/*`, and the thin route wrappers.
