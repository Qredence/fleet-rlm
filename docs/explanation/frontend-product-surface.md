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
5. The run workbench hydrates from the execution summary and final artifact.
6. The user can inspect volumes or settings from the same shell.

## Surface Map

| Surface | Route | Owns | Notes |
| --- | --- | --- | --- |
| Workbench | `/app/workspace` | `features/workspace/*` | Main execution and inspection surface |
| Volumes | `/app/volumes` | `features/volumes/*` | Mounted Daytona volume browser |
| Settings | `/app/settings` | `features/settings/*` | Dialog-first settings surface |

## Layer Structure

```text
src/frontend/src/
├── routes/                # Thin TanStack Router wrappers
├── features/
│   ├── layout/            # Shell chrome, route sync, dialogs, sidebar, header
│   ├── workspace/         # Workbench UI, transcript, inspector, run panel
│   ├── volumes/           # Volume browser and file preview
│   └── settings/          # Settings dialog and runtime forms
├── lib/
│   ├── workspace/         # Zustand stores, event adapters, hydration reducers
│   └── rlm-api/           # REST and websocket clients
├── stores/                # Shell/navigation state
├── components/ui/         # Shared UI primitives
├── components/ai-elements/ # AI Elements rendering primitives
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

## Canvas Behavior

The shell canvas is route-aware.

- On Workbench, it acts as the inspector and run workbench.
- On Volumes, it shows the file preview.
- On Settings, it closes.
- On mobile, the canvas becomes a bottom sheet.
- On desktop, the canvas is the right-hand resizable panel.

## What Is Out Of Scope

The current frontend does not treat these as product surfaces:

- `taxonomy`
- `skills`
- `memory`
- `analytics`
- `history`
- `optimization`

New work should target `features/*`, `lib/*`, and the thin route wrappers.
