# F live canary ledger

Synthetic-only run through the browser TUI served by `uv run fleet web`. Session: `f-canary-20261030-1900`. MLflow experiment: `fleet-f-canary-20261030-1900` (local experiment ID 47). API/UI/MLflow ports: 18000/18080/15001. Effective mode started Hybrid; Turns 2 and 4 used RLM, Turn 3 used Flex. Turn timeout: 120 seconds. Child limits: one active, two total. Shared-notes mount disabled.

## Submitted TUI Turns

| # | Turn ID | Trace ID | Result |
|---|---|---|---|
| 1 | `ff6d8f60df8b40a98a66ad76e0b25def` | `tr-66931fd18127b92e8434afd628158c3d` | Direct smoke completed and persisted; 19.378 s. |
| 2 | `206c198930b84a288860f94bbc5d8108` | `tr-a28ca7925cfff735042b7f78c8e502a5` | RLM wrote one 19-byte file and UI showed byte-exact readback; 16.472 s. Ctrl+O expanded the REPL cell. |
| 3 | `c226b35ba32a4e3081234521ae3735b7` | `tr-a700fe550f8c96edfd6c349281baea84` | Flex wrote one 20-byte file through a child and showed promoted path and readback; 40.466 s. |
| 4 | `7bde397b64fb4e0f826bb9ad721e8bdf` | `tr-3152752d94d28a4b167028f86ac68a36` | Explicit child produced two files; both promoted successfully and appeared in completion inventory; 58.924 s. |
| 5 | `c9b72a66e2014385b5a8dd2088cc0ed3` | `tr-44f03a1ccf9f318882ca979892034934` | Cancelled at 122.438 s, cleanup `stopped`. The child actually succeeded and its file was promoted; intended child-failure case was **not exercised**. |
| 6 | `f29b4258e59447a9aeba6876f7f7f62d` | `tr-e653c0933ee94481c710707b84b07dee` | Cancelled at 130.861 s, cleanup `stopped`. The 101-output quota path was **not exercised**. |
| 7 | `65cbf261833d4aeda7317460cddc33e2` | `tr-3d62fa5df8c92ae6c791a29f57a6f481` | RLM execution cancelled in the TUI; `/turn` confirmed `cancelled`, cleanup `stopped`. No interrupted code was replayed. Root `89488755-bf6a-41f3-aa1f-7c452f6669e6`. |
| 8 | `84b46d35f75b492dbe4b7183d0ff0b71` | `tr-85d23b56a477e09b492068ed4b680d12` | RLM recovery completed on a fresh root; `files/f7-recovery.txt` read back as 24 bytes, SHA256 `d3347cb0663899870add3b8ebd6de9e9dddd05a9802983d8c04fa3fca25e65f0`. Root `f668a849-7402-463b-a41b-88fa3f4eaa65`. |
| 9 | `64271a5a5db5445c99df8759c8dfe53e` | `tr-de5e2876536a6d8333ff1ec8db2f7cfc` | Owned API PID 30020 was killed while execution was in progress. On same-Session restart, the previous root was reconciled to `STOPPED`, ownership epoch advanced to 2 with no pending resources, and this Turn initially read `running`. After adding epoch-fenced recovery, the canary Store method marked it `failed`; readback confirmed the no-replay result. |
| 10 | `2cd923fc8fdf458aa6ca275c574632bc` | `tr-b7649f844f0cc172cdcafa8f507b451f` | RLM recovery completed on new root `465f0cdd-d179-452d-b98f-1cfe62770856`; `files/api-restart-recovery.txt` read back as 29 bytes, SHA256 `5e1f0f2a1d4eae4a47b8ad507ca92c80a979abad5e5eb9e5aab5ff4e3ffc0c45`. |
| 11 | `8baed38f0a7b492682e8711a1300efb8` | `tr-4a988c7f06c4ddbc09d60efb51c48450` | Flex explicit child task cancelled after repeated `Child sandbox capacity is exhausted` errors; `/turn` confirmed `cancelled`. No complete child publication or successful capture. This unexplained capacity failure stopped the matrix; T12 was not submitted. |

No extra model Turns were submitted to replace failed matrix cases. The intended child-failure and quota cases remain unqualified. T11 did not exercise canonical root-plus-child completion. The warm-follow-up T12 was omitted after the T11 capacity failure, as required by the stop-on-unexplained-failure rule. Token count from completed UI summaries was 120,555 across Turns 1–5; usage for later Turns and monetary cost are unknown.

## Provider resources and cleanup

- Canary root sandbox IDs `a6b713db-5e25-4824-ac81-c5735c99e624` and `645b8af1-9ba1-4f54-a9b7-f79abc1a999c` were both confirmed `STOPPED` by Daytona label lookup. They are non-disposable Session roots and were retained.
- Daytona label lookup after teardown confirmed all five roots for both canary Sessions were `STOPPED`: `645b8af1-9ba1-4f54-a9b7-f79abc1a999c`, `a6b713db-5e25-4824-ac81-c5735c99e624`, `465f0cdd-d179-452d-b98f-1cfe62770856`, `f668a849-7402-463b-a41b-88fa3f4eaa65`, and `89488755-bf6a-41f3-aa1f-7c452f6669e6`. No child sandbox appeared in either canary Session's label query.
- Publication probe sandbox `9ee35300-3cdf-4c0e-a971-116194978648` was deleted; a subsequent label lookup returned zero probe sandboxes.
- Probe staging prefix `f4eb24985147409b9646095c0215362d` was removed and remote readback confirmed it absent.
- Owned Fleet API, web TUI, and MLflow listeners on 18000, 18080, 18081, and 15001 were stopped. T11 had reached durable `cancelled` status before shutdown; T9 was durably `running` at teardown and was later terminalized as `failed` by epoch-fenced recovery, with readback confirming the result.
- Monetary cost is unknown. No GEPA run was started.

## Screenshots

Browser screenshots were saved under `/Users/zocho/.agent-browser/tmp/screenshots/` during interaction. They include non-secret runtime configuration and synthetic canary output; inspect locally rather than copying them into source control.


## Remediation canary — 2026-10-30 21:36–22:08 CET

Authorization: the operator authorized live runs for this session and directed the UI path
(`uv run fleet web`) with MLflow experiment 1 (`fleet-rlm-dev`, local tracking
`http://127.0.0.1:5001`). Every Turn was submitted from the browser TUI; no Turn was scripted.
The API under test was started from this working tree on port 8003 and the web client on 8083.

Sessions: `sess_web_539681db52da` (L1-L5, L7) and `sess_web_20ce23e82f95` (L2 rerun after the
ordering fix).

| ID | Turn ID | Trace ID | Result |
|---|---|---|---|
| L1 | `07e71126d1d7…` | `tr-38a3be19529bd6c5edc23f8ed81dfd68` | Fresh admission. Durable `sandbox_ownership` = epoch 1 with the root's stop intent and provider id recorded before confirmation. `direct` route, completed 25.99 s. |
| L2 | `1c0151e27c1d…` | `tr-23daf84a0a1cde65ed6a426c41414234` | RLM wrote 40 small files plus a 300000-byte CSV. Turn **completed**; capture `complete: true`, 41 paths, `large.csv` `size_bytes: 300000`. Under the previous 32-file / 64 KB / 256 KB evidence limits this Turn would have failed. |
| L3 | `45e20e58a561…` | `tr-a8ffe32cef40b4a58be926fa181f4417` | Child asked to write 101 outputs. Promotion refused with `Child output promotion quota exceeded`; 0 of 101 promoted, no `artifacts/<agent-id>/` directory, capture unchanged at 41. |
| L5 | `836b63f8a0dd…` | `tr-c519a12ac704686c2fa535386acc27c6` | Turn cancelled from the UI: durable status `cancelled`, `cleanup: stopped`, ownership emptied, no replay. |
| L7 | — | — | Seeded one resource whose provider id does not exist, then admitted: `200`, record removed, epoch advanced to 2. Seeding an invalid record instead answered `503 Session runtime is unavailable`, so genuine failures still fence. |
| L2b | `…` | `tr-51cc9e43e98202678714debcfbd53615` | Rerun after the ordering fix (below). 41 evidence files retained; `route: rlm`, `lm_calls: 10`, `cleanup: not_required` all present. |

L4 (failing child) and L6 (legacy refusal) were not run as UI Turns: L6 was exercised directly
against the API — a pre-migration row (`sandbox_ownership IS NULL`) answered `503` while the
owned row answered `200`, then the seeded row was restored to `NULL`. L4 remains unqualified in
this canary.

### Defect found by trace analysis and fixed

Reading the L2 trace with the MLflow tools surfaced a real ordering bug the offline suite had
missed: the root `fleet_turn` span recorded `execution_evidence` before `cleanup`, and MLflow's
span sanitizer has a fixed node budget (`observability._TRACE_NODE_LIMIT = 200`). With 41
artifacts the evidence tree consumed the budget and `cleanup` was exported as
`[trace size limit]`. The scalar facts (route, usage, cleanup) now precede the evidence tree so
truncation can only shorten the evidence, never the Turn's own record. The first attempted fix —
clipping the trace copy of the evidence — was rejected because it broke
`artifact_correctness` scoring against reviewed gold, which the suite caught.

L2b confirms the fix on the live path.

### Provider cleanup

`sess_web_539681db52da` roots `6a9ed93f-194b-458c-9053-8cf6286135cd` and
`6cf7159a-e316-4bd5-afc7-340c5ec10233` are `stopped`; `sess_web_20ce23e82f95` root
`050714f4-d5be-4f88-b6d1-d7934209aecb` was stopped by eviction and its ownership record cleared.
Owned API (8003), web client (8083), and the MLflow listener were stopped after the matrix.
Monetary cost is unknown. No GEPA run was started.
