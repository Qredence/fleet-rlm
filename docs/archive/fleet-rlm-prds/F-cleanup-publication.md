# PRD F — Cleanup and publication

**Status:** proposed; four separately reviewable stages F0-F3, not one large PR. **Priority:** release-critical before unattended use. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10). [Index](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

Users must not have a new root process write into a Session subtree while an old root may still be running. Failed child work must not leave partial outputs that appear to be successful published artifacts. Operators need cleanup uncertainty to survive runtime eviction and process restart.

**current-source (input PRD):** `cancel_turn` fences host replies, cleans children and attempts root stop. Root handles/contexts are cleared even when stop fails. A later Turn can provision a replacement against the same session workspace. General `shutdown()` also discards a root handle; its root loop does not aggregate child `False` results into its returned confirmation. [Cancellation/provisioning](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/sandbox/manager.py#L651-L797), [shutdown](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/sandbox/manager.py#L1196-L1265), [registry eviction](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/session.py#L490-L527).

Children already retain failed-delete handles and allocation journals for reconciliation. Preserve that useful mechanism; it does not establish confirmed root termination or a complete cross-worker Session reuse fence. A database Session lease proves current local ownership, not that a previous remote root has stopped.

**current-source (input PRD):** child artifacts are copied one file at a time into the parent; success receipt appears only after the loop. A later upload failure can leave an earlier file visible without a success receipt. Evidence digests and final artifact inventory are gathered at different points. [Publication and evidence](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/sandbox/workspace_io.py#L300-L354), [child failure handling](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/children.py#L93-L110), [commit inventory](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/session.py#L175-L178).

**previously-reproduced (not rerun):** the supplied review reports a fake-provider path allowing root reuse after unconfirmed stop and partial parent writes before a child quota rejection. These are historical reports; supporting executable probe artifacts were not included in this revision's source package. Regenerate the relevant fault tests at the implementation SHA. **hypothesis:** crash/restart publication inconsistencies follow plausible source paths but are not claimed as observed production incidents.

## 2. Native-framework-first decision and rationale

Retain native DSPy RLM's interpreter factory, the existing Daytona manager, host/remote filesystem helpers, Store transactions/leases and native FastAPI framing. None of these alone confirms a remote stop or publishes an entire selected child-output set. That is the bounded Fleet responsibility; it does not justify a new scheduler, resource service or generic distributed transaction framework.

Start with the existing identities, allocation journals, Session leases and cleanup call sites. Reuse them before adding persistence. For publication, verify actual provider/mounted-filesystem behavior before choosing **one** visibility authority: an atomic directory publication, or an authoritative committed manifest respected by all supported readers. A receipt can describe a publication without becoming a second independent commit mechanism.

## 3. Scope and non-goals

| Stage | Deliverable | Prerequisite | Limit of its guarantee |
|---|---|---|---|
| **F0** | Immediate unresolved-resource fence and truthful cleanup | Existing manager/registry paths | Protects the live process only; does not establish restart safety |
| **F1** | Minimum durable ownership and bounded recovery | F0 plus inventory of current Store/provider identifiers | Restart/takeover safety only after crash-window tests and provider verification |
| **F2** | Complete selected-child publication with one visibility boundary | Full preflight, generation fencing and provider semantic decision | Atomicity applies to the documented publication/read interface, not a distributed filesystem/SQL transaction |
| **F3** | One finalized logical artifact view for the canonical result | B and F2 | Captured metadata/evidence agreement; not immutable byte storage by itself |

Deliver each stage in a separate PR by default. F0 must not wait for B, C, G, a migration or a provider rename investigation. F1 and F2 must not be combined into one database/provider migration. F2 may progress independently after its fencing/provider gates; F3 is the integration after publication and canonical-result ownership are settled.

Non-goals: replay interrupted generated code, create a perpetual reconciler, promise exactly-once remote effects, introduce a cleanup wizard, redesign the Volume layout wholesale, version all workspace files, or run two competing publication protocols. The prohibition on unnecessary abstractions does not excuse a missing durable fence: add the smallest persistent fact if existing data cannot represent it.

## 4. Affected code tree and stage ownership

```text
src/fleet_rlm/
  sandbox/manager.py             # F0/F1: retained identities, interruption, admission
  sandbox/repl.py                # F0/F1: preserve existing generation reply fences
  session.py                     # F0/F1: registry eviction, lease-safe admission
  store.py                       # F1 only if existing data cannot hold required identity
  sandbox/workspace_io.py        # F2: preflight/staging/publication; F3: capture once
  sandbox/runtime/fs_ops.py      # F2 only for a verified provider/filesystem operation
  engine/children.py             # F0 cleanup result; F2 publication commit ordering
  engine/turn.py                 # F0 acknowledgment; F3 canonical snapshot handoff
  execution_evidence.py          # F2 reader audit; F3 use the captured view
  api/schemas.py                 # only proven/additive public cleanup facts with C
  api/routes/{turns,workspace}.py # admission and supported published-reader contracts
  contracts.py                   # reuse existing evidence types; no hierarchy required
 tests/sandbox/{test_manager,test_workspace_files,test_repl}.py
 tests/api/test_turns.py
 tests/lite/test_session.py
 tests/store/test_store.py
```

The tree identifies potential edits, not an instruction to modify every file. Record exactly which stage changes each owner; do not relocate modules for this work.

## 5. Requirements, tasks and design

### F0 - Immediate fence and truthful cleanup

**Rationale:** missing a local active handle is not evidence that its former remote process stopped.

**F0.1** Retain unresolved root/allocation identity and intended stop/delete action after an unconfirmed operation. Fence reply generations and interpreter work, but do not clear the information used to deny a replacement writable root. Provisioning and writable workspace entry points must inspect unresolved identity, not only `self._sandbox is None`.

**F0.2** Request root interruption promptly after the host fence, without waiting behind serial child deletion. Attempt every required child/root cleanup even when one returns false or raises. Aggregate confirmations truthfully. Use existing locking and bounded lifecycle paths; do not create a cleanup executor framework. Serialize concurrent shutdown callers or make them observe the same in-flight operation.

**F0.3** Keep uncertainty visible through in-process runtime eviction/reconstruction. Reuse the existing registry owner to deny recreation or retain the unresolved manager until reconciled. Do not claim this in-memory safeguard survives process restart. Error delivery may report unconfirmed cleanup after bounded attempts; it must not imply resources are reusable.

```python
# Control-flow sketch: reuse existing owners, locks, result types and time bounds.
fence_host_calls_and_generations()
root_result = attempt_root_interruption()  # retain identity if unconfirmed
child_results = attempt_all_required_child_cleanup()
confirmed = root_result.confirmed and all(r.confirmed for r in child_results)
if not confirmed:
    retain_unresolved_identity_and_deny_writable_reuse()
# Returning an error is permitted; provisioning a replacement is not.
```

Expected outcome: no new mutable root in the same process while required cleanup remains unresolved. F0 can merge independently; unattended restart safety remains blocked on F1.

### F1 - Durable recovery only where existing ownership is insufficient

**Rationale:** an in-memory fence disappears on process failure; a Session lease identifies the current host owner, not confirmed provider shutdown.

**F1.1** Produce a concise mapping of existing Store Session/lease/intent fields, provider IDs, create labels/nonces and allocation journals. Exercise recovery using these facilities first. Add **at most one minimal logical ownership/cleanup record in the existing Store domain** only if the mapping and crash tests demonstrate missing durable information. Prefer existing fields over a new table; justify any schema addition with the exact invariant it supplies.

**F1.2** Preserve enough information to recover the Session/subtree, provider resource or allocation identity, existing ownership/generation token, pending action and confirmation. Journal create intent before allocation when required by the provider protocol; attach the known provider ID immediately afterward. Persist cleanup intent before the operation whose acknowledgment can be lost. Do not store secrets or duplicate the entire manager state.

**F1.3** Under the existing lease, use bounded admission/cleanup reconciliation to confirm the old resource state before writable reuse. Cover create-before-ack, stop-before-ack, crash-before-confirmation, eviction and takeover. A late response from an old owner cannot clear a newer intent. Empty or ambiguous lookup is not confirmation. Document which authoritative provider response, including confirmed absence where supported, proves the original resource cannot still write. No blind replacement or rerun of the interrupted Turn is allowed.

Expected outcome: the next process/owner can find and reconcile unresolved resource identity. No new always-running reconciler, scheduler or recovery service is introduced. Provider ambiguity remains a fenced, diagnosable state rather than being hidden.

### F2 - One child-publication authority

**Rationale:** copying N files and then writing a receipt does not make the set atomically visible.

**F2.1 - Preflight first.** Enumerate the complete selected set and validate paths, allowlists, collisions, count, sizes and total bytes before the first public copy. Retain byte/digest checks during staging because files may change after enumeration. A deterministic quota failure must produce zero public writes. This small repair can land before stronger publication guarantees; label its limit honestly.

**F2.2 - Choose from demonstrated semantics.** Test the actual mounted Daytona Volume and relevant API/read paths, not only a local POSIX directory. Record the selected strategy and scope of readers:

| Strategy | Required evidence | Single publication commit point |
|---|---|---|
| **Directory publication** | Same-filesystem directory move/rename has the required atomic visibility and recovery semantics for all relevant readers | Verified final-directory publication |
| **Manifest visibility** | Directory publication cannot satisfy the requirement, and every supported listing/read/evidence path can enforce one authoritative committed manifest | Committing that manifest |

Select one. Do not require both a filesystem rename and an independently authoritative manifest transaction to declare success. In the directory strategy, metadata inside the stage may support receipt recovery; it is not a second publication decision. In the manifest strategy, uncommitted objects are staging only. If raw readers can bypass the manifest, this strategy does not promise atomic raw-filesystem visibility to them; route those supported readers through the boundary or do not claim that guarantee. If neither approach meets the task without a broader subsystem, stop at the verified preflight repair and document the separate gap.

**F2.3 - Stage, verify and publish.** Use a private attempt prefix outside supported published inventories. Verify the complete output set, then serialize its one commit point against cancellation/generation fencing. Cancellation first means no committed set; publication first means its receipt remains valid even if later cleanup is uncertain. Retry by publication identity/digest, reject incompatible existing destinations, and resolve ambiguous publication by authoritative readback instead of blind recopy. Do not mark failed child execution as successfully published.

**F2.4 - Keep metadata internal.** Where the chosen strategy needs a receipt/manifest file, reserve its name, prevent child collisions, exclude it from user listings/downloads/counts/evidence/quotas, and avoid a self-referential digest. Recovery verifies host-held identity and hashes; a model-written filename is not trusted provenance. Audit all existing artifact readers, including `execution_evidence.collect_artifacts`. Legacy output has an explicit read policy rather than fabricated new-format receipts.

Expected outcome: after the selected publication protocol is qualified, supported readers see no newly committed outputs or the entire committed set, with a host receipt. Partial staging may exist but is not published success. This is not one atomic transaction across the provider filesystem and PostgreSQL.

### F3 - One logical artifact snapshot for B

**Rationale:** canonical result fields and evidence should describe the same capture, not separate inventory passes.

**F3.1** Quiesce/finalize managed publishers and capture eligible root outputs plus committed child publications once: sorted paths, sizes, digests and provenance using existing evidence types. Validate current limits. Derive `artifacts`, `artifact_changes` and execution evidence from this view and pass it to B's canonical completion rather than relisting inside SessionRuntime.

**F3.2** Preserve the captured view as logical result data; avoid mutation by later diagnostics. Record the exact capture/fence boundary. Do not claim immutable bytes unless versioned/hashed output retention and remote-write fencing actually provide them. An external writer may still mutate ordinary workspace content; a recheck can detect change but is not a perpetual immutability guarantee.

**F3.3** Keep publication status, Turn commit status and remote cleanup status separate. A successfully published set is not erased by later cleanup uncertainty; a child receipt is not proof the whole Turn committed. B must carry existing facts without inventing a new status hierarchy.

Expected outcome: current and replayed canonical results agree with the captured publication evidence. Immutable object storage or a generalized snapshot subsystem is outside this stage.

## 6. Before / after flow

Before: fence host replies -> serial child cleanup -> root stop -> discard identity on uncertainty -> later root may reuse the subtree. Child files are individually copied -> partial public files can precede a receipt. Evidence and terminal inventory can describe different captures.

After F0: host fence -> prompt root interruption and all required cleanup -> confirm or retain identity -> deny same-process unsafe reuse.

After F1: persist only the needed ownership/intent -> operation -> confirm or keep unresolved -> bounded lease-safe admission recovery after restart.

After F2: complete preflight -> private verified staging -> one chosen publication commit -> receipt -> supported readers. Failure before commit leaves no newly committed set.

After F3: finalize managed publication -> capture one logical view -> B's canonical transaction -> consistent live/replay evidence. These guarantees accumulate; F0 alone is not described as F1-F3 completion.

## 7. Stage-specific acceptance tests and measurable outcomes

| ID | Fault/scenario | Passing condition |
|---|---|---|
| F0-T1 | Root stop/delete unconfirmed, then another Turn or writable entry | No replacement mutable root; unresolved identity retained and retry possible |
| F0-T2 | Child returns false, raises or blocks | Root interruption is initiated without avoidable serial-child delay; all required cleanup attempted; no false confirmation |
| F0-T3 | Concurrent shutdown and in-process eviction/reconstruction | Same unresolved resource cannot escape the registry fence; response/cleanup semantics remain truthful |
| F1-T1 | Crash after create intent, after create-before-ID acknowledgment, after stop-before-confirmation | New owner locates/reconciles intent using selected durable data; no blind duplicate allocation |
| F1-T2 | Eviction/takeover and late old-generation reply | New lease owner sees uncertainty; stale confirmation cannot clear newer ownership |
| F1-T3 | Legacy Session has no new-format record | Provider-aware reconciliation or refusal, not treating missing provenance as proof no compute exists |
| F2-T1 | Count/bytes/path quota rejected during complete preflight | Zero public writes, including the 101-versus-100-file case |
| F2-T2 | Fail a staged upload; crash before/after the chosen commit point | No newly committed set or the complete set under the selected reader contract; retry/readback is idempotent |
| F2-T3 | Publication acknowledgment/receipt lost; metadata-name collision | Host-verifiable receipt recovered without duplicate/conflicting publication; metadata excluded from public inventory/evidence |
| F2-T4 | Cancellation races with publication and later cleanup failure | Exactly one generation-valid visibility decision; post-publication cleanup uncertainty does not erase receipt |
| F3-T1 | Mutation between old independent evidence/inventory locations | New code captures once; every canonical field derives from that view or rejects detected invalidation |
| F3-T2 | External mutation after capture | Report states captured logical facts and the actual byte-retention guarantee; it does not claim immutable content without evidence |

Use current sandbox/Store fakes and focused manager/workspace/Turn/session tests, then repository gates. F1 requires controlled subprocess crash/restart evidence using a durable fake or authorized test database. F2 requires provider-semantic evidence before asserting provider-level atomicity. Live canaries require explicit authorization under repository rules; local filesystem tests are not proof of remote Volume behavior.

Record zero admitted unsafe reuse, false cleanup confirmations, partial committed sets and logical inventory/evidence mismatches across the applicable fault matrix. Measure root-interruption ordering, not an invented provider latency SLA. For each stage report added persistent fields, custom lines/branches added and deleted, and the precise guarantee still deferred.

## 8. Stage-specific migration, rollout and rollback

**F0:** ordinarily no SQL migration. Deploy the manager/registry fence together. A code rollback must not resume writable work for resources still unresolved; retain the guard or deny admission until reconciliation. F0 does not authorize unattended restart safety.

**F1:** first prove whether existing fields suffice. If not, use one minimal additive representation in Store. Deploy admission/read compatibility before creating new records. Reconcile existing active/legacy Sessions without fabricating IDs. A binary that ignores durable uncertainty is not a safe rollback for writable operation: keep the reader/fence or keep admission disabled. Never erase unresolved intent or drop its persistence merely to roll back.

**F2:** document the chosen publication format and legacy reader policy. Deploy all supported reader changes with the writer, preserving existing user outputs. On rollback, keep reading valid new-format committed output and hiding abandoned staging. Do not fall back to legacy scanning that exposes partial stages. A manifest is versioned only when a real format change requires it; avoid speculative metadata hierarchies.

**F3:** keep B's canonical result backward-readable and maintain the logical view stored for completed Turns. Roll back capture integration coherently with its writer/reader assumptions; never reconstruct old completion evidence by relisting a changed workspace.

## 9. Risks, dependencies and decision gates

F0 is independent and release-critical. F1 maps and proves restart durability before unattended operation. F2 depends on its specific provider semantic proof and cancellation fence, not on a large F1/F2 combined redesign. F3 depends on B and the accepted F2 publication contract; B does not wait for F3 to remove duplicate completion assembly. C documents actual resulting fields when they exist. D/E must not hide unresolved cleanup in evaluation outcomes. G remains optional and cannot mask a cleanup failure.

The main overengineering risk is creating a new service or duplicate commit mechanism for every failure window. Require an explicit existing/native insufficiency and smallest-owner change at each gate. The main correctness risk is weakening the guarantee merely to avoid a justified persistent fact. Tests and provider evidence decide between those outcomes; file count does not.

## 10. Codex-ready checklist

- [ ] Inspect current manager/registry/Store/journal paths and freeze relevant lifecycle/publication fixtures.
- [ ] Implement F0 first; prove same-process denial and truthful aggregate cleanup independently.
- [ ] Inventory existing durable data before proposing any F1 schema change; run the crash/takeover matrix.
- [ ] Land full-set F2 preflight, then qualify exactly one publication strategy using actual provider semantics.
- [ ] Audit every supported reader and internal metadata path for that strategy; test ambiguous publication recovery.
- [ ] Integrate the F3 logical snapshot into B without a second inventory or immutable-bytes claim.
- [ ] Keep separate PRs/acceptance records for F0-F3 and report remaining guarantees after each.
- [ ] Preserve fences and compatible readers during rollback; no unattended-readiness claim from F0 alone.
- [ ] Complete the [common acceptance report](README.md#acceptance-report), including every added durable field and provider gate.
