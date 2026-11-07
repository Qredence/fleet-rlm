# F0 implementation and acceptance

This change implements **F0 only** from [PRD F](F-cleanup-publication.md).
F1–F3 remain open and require separate reviewable stages. No unattended restart
or atomic child-publication guarantee is claimed.

## Coordinates and scope

- Start SHA: `4536ae9d9a30dc6f792249e35f85f2dabe208e06`.
- Final artifact: the uncommitted working-tree diff based on that SHA; no commit,
  push, deployment, live provider run, or database run was performed.
- Existing untracked `.codex/` content is unrelated and preserved.
- Locked integrations: DSPy 3.4.1, Daytona 0.224.0, FastAPI 0.162.0,
  GEPA 0.1.4, MLflow 3.17.2, Pydantic 2.13.5. The lockfile is unchanged.

## Owners and contract

`sandbox/manager.py` retains the existing provider handles, allocation labels,
execution reply fences, shutdown lock and per-Turn teardown owner. A single
in-memory `_cleanup_pending` flag distinguishes a reusable handle from a resource
still awaiting stop/delete confirmation; budget reset cannot clear it. Cancellation
now uses the same cleanup implementation as general shutdown. Root interruption
precedes serial child cleanup. Every child is attempted and its confirmation is
included in the result.

`session.py` retains the existing registry and Session lease. Eviction and registry
shutdown keep unresolved runtimes fenced and retain their leases, denying local
reconstruction until cleanup succeeds. Confirmed runtimes can be released even if
another runtime remains unresolved. No new service, scheduler, executor framework,
public response model, durable field or migration is introduced. The existing
`stopped`, `unconfirmed` and `not_required` Turn vocabulary is unchanged.

The duplicate cancellation-specific provider loop is removed. Cleanup remains one
manager method under the existing shutdown lock; registry close reuses its existing
bounded thread pool. Added branches distinguish pending create lookup, missing
client/ambiguous lookup, aggregate child failure and retained registry ownership.
These branches supply the unsafe-reuse invariant rather than a new lifecycle model.
Runtime diff: 107 lines added and 83 deleted across the two existing modules,
including comments/docstrings. There are no moved modules or functions. AST counts
change from 87 to 88 function definitions (one nested registry-close helper),
125 to 133 `if` statements, and 20 to 21 exception handlers. Added persistence:
zero fields, tables or migrations. Regression coverage accounts for the larger
test diff separately from runtime code.

## Offline fault evidence

Focused regressions live with the existing manager and Session tests:

| PRD requirement | Exercised scenario and assertion |
| --- | --- |
| F0-T1 | Root stop raises, handle remains, Turn reset cannot provision/write/allocate; later confirmed shutdown permits fresh provisioning |
| F0-T1 | Create acknowledgment lost; empty lookup stays unresolved; one matching resource is stopped before reuse |
| F0-T2 | Root stop is observed before a blocked child completes; acknowledgment still waits for child cleanup |
| F0-T2 | Child returns false or raises; sibling cleanup still runs and aggregate result remains unconfirmed |
| F0-T3 | Concurrent root/child shutdown callers wait for provider cleanup; confirmed provider action is not duplicated |
| F0-T3 | Eviction failure retains runtime/lease, subsequent admission cannot reconstruct until cleanup succeeds |
| F0-T3 | Registry close attempts all runtimes, releases confirmed ones and retains unresolved ones for retry |

Focused validation: `uv run pytest tests/sandbox/test_manager.py
tests/lite/test_session.py -q` passes all 171 tests. Full `make check` passes:
1,823 tests passed, one skipped, 84% aggregate coverage, Ruff, formatting,
type checking, documentation links, OpenAPI contract and distribution build.
`git diff --check` also passes. The first full run was interrupted after a stall
with 1,513 passing tests; the workspace tracing file passed independently, and
the complete rerun with `PYTEST_ADDOPTS='-o faulthandler_timeout=30'` passed
without changing async logging or runtime configuration. These tests
use provider fakes and establish ordering and local denial, not provider latency,
availability, cost, or restart durability. Existing ordinary API/direct/RLM/Flex
and evaluation tests run in the full offline gate; no live service is required.

## Existing durable information and F1 gap

| Existing facility | Information it carries | Missing proof for restart admission |
| --- | --- | --- |
| Store Session row | Session ID, memory, stored Turn history, environment-observation marker | No authoritative current resource or unresolved cleanup intent |
| Store Session advisory lease | Current dedicated connection's ownership of a Session | Cannot prove a previous remote process stopped after connection loss |
| Store Turn intent/result | Turn/idempotency identity, status, canonical result and historical root ID when recorded | Not written before allocation/cleanup; not current resource ownership |
| Local allocation journal | Session/agent labels, known sandbox ID, requested/ready status, expiry | Local disk may not follow another worker; no lease/generation-fenced cleanup confirmation |
| Daytona create labels | `fleet_session`, `fleet_agent`, `fleet_parent` on a resource | Empty/ambiguous listing is not established proof of absence |
| In-memory manager | Handle, create-pending flag, Turn generation, cleanup fence, intended action via parent/disposable policy | Lost on process exit |

F1 must test create-before-ack, stop-before-ack, crash-before-confirmation,
takeover and stale-owner responses using durable recovery fixtures before choosing
the smallest Store representation. This F0 change adds **zero persistent fields**
and does not treat an expired journal or missing record as a restart-safety proof.

## Publication and capture gates

The current child-promotion loop can write earlier files before rejecting a later
quota excess. F2.1 must preflight the complete selected set before public copies.
Transfer failures can still leave partial copies after that bounded repair.
Stronger F2 requires actual mounted Daytona Volume/read-path evidence before
selecting either verified directory publication or one authoritative manifest.
No live provider authorization was supplied for that experiment. Reader changes
must include `execution_evidence.collect_artifacts`; raw scanning currently has no
internal publication-metadata policy. F3 follows the accepted F2 contract and
canonical-result ownership, capturing one logical view rather than relisting.

F0 checks admission and interpreter/reply fencing; it does not drain a host file
transfer that already passed admission before shutdown. Stop/delete confirmation
is about the remote resource. Transfer/publication generation fencing and its
provider semantics remain part of F2; this change does not assert atomicity for
uploads already in flight.

## Later changes to these files

`manager.py`, `session.py`, and `store.py` gained F1 and F2 work after this report was written,
so the AST counts, line deltas, and coverage figures above describe the F0 snapshot only and no
longer reproduce against the current tree. Two later edits specifically revised F0 behavior:
`_shutdown_locked` now treats a provider not-found answer as confirmed cleanup during
reconciliation, and `reconcile_sandbox_ownership` returns early when nothing is outstanding.
See the F1 report and the remediation canary in [the ledger](F-live-canary-ledger.md).

## Rollout and rollback

Deploy the manager and registry guard together. Cleanup uncertainty is diagnosable
as `unconfirmed` and remains retryable through shutdown/eviction. A code rollback
that discards unresolved identity would restore unsafe writable admission; retain
the fence or deny admission until reconciliation. No SQL rollback is needed.
Process restart still loses this fence: F1 remains required before unattended use.
