# Phase 3: orchestration seams around websocket transport

## What was isolated in this phase

- `src/fleet_rlm/api/orchestration/repl_bridge.py` now owns the temporary REPL callback bridge that still fans interpreter callbacks into websocket lifecycle persistence.
- `src/fleet_rlm/api/orchestration/hitl_policy.py` now owns HITL command-resolution policy so websocket command transport only parses frames and sends the resulting envelopes.
- `src/fleet_rlm/api/orchestration/session_policy.py` now owns session-switch and manifest-restore policy so websocket transport no longer embeds restore/reset decisions inline.
- `src/fleet_rlm/api/orchestration/terminal_policy.py` now owns terminal persistence ordering and completion policy while websocket transport still owns event serialization and socket send/close behavior.
- `src/fleet_rlm/api/orchestration/startup_status.py` now owns the delayed startup-status heuristic used to protect the frontend first-frame timeout.

## What websocket transport still owns

- Socket accept, close, disconnect, and receive loops.
- Auth-derived identity and message/session extraction.
- Worker-request construction and worker stream invocation.
- Websocket envelope serialization and event delivery.
- Persistence calls that are still required by the current product contract.

## What still remains temporarily inside fleet-rlm

- The REPL bridge still depends on websocket lifecycle persistence because interpreter callbacks are not yet emitted as fully worker-native events.
- HITL resolution still stops at websocket-visible compatibility events; a future outer orchestration layer should own approval continuation and retry/checkpoint flow.
- Session switching still restores directly into the chat agent because there is not yet a separate outer session/workflow orchestrator.
- Terminal completion still finishes lifecycle records from the current websocket path because the worker remains a one-task execution seam, not a multi-step workflow host.

## What Phase 4 should move outward

- Approval/HITL continuation flow after command resolution.
- Checkpoint, resume, and workflow-continuation policy beyond one worker turn.
- REPL callback fan-out once interpreter/runtime events can be surfaced through a broader orchestration layer.
- Session/workspace restore coordination that spans multiple turns or future multi-worker workflows.

## Behavior-preservation caveats

- The websocket envelope and payload shapes remain unchanged.
- The delayed startup status still emits the same status text and payload.
- Final completion summary semantics and terminal ordering remain unchanged.
- The worker boundary and Daytona passthrough remain the execution seam; this phase only narrows where orchestration policy lives.

