# Fleet RLM clean-backend code-review contract

## 1. Purpose

This is the blocking review rubric for the clean backend:

```text
FastAPI SSE
  -> stateful dspy.RLM
  -> Daytona Sandbox + mounted Volume
```

## 2. Severity

- **Blocker**: can violate tenant isolation, corrupt committed state, execute generated code on the host, break the primary path, leak secrets, or falsify evidence.
- **Major**: breaks a required contract, lifecycle guarantee, budget, recovery path, or acceptance criterion.
- **Minor**: maintainability, readability, local test quality, or documentation issue without current contract impact.
- **Suggestion**: optional improvement outside the requested scope.

A phase cannot close with an open blocker or major finding.

## 3. Review preconditions

The change description must state:

- owning phase and ticket;
- user-visible behavior;
- files changed;
- contracts changed;
- tests run;
- live evidence required or not required;
- rollback or recovery behavior;
- known limitations.

Reject a status-only update without matching evidence.

## 4. DSPy and RLM review

- [ ] `dspy.RLM` remains the primary runtime.
- [ ] No generic agent abstraction duplicates DSPy.
- [ ] One fresh RLM exists per concurrent turn.
- [ ] Root and sub-model roles are explicit.
- [ ] `sub_lm` serves bounded semantic calls.
- [ ] RLM constructor compatibility is contract-tested.
- [ ] `dspy.History` derives from committed turns.
- [ ] Budgets map to actual RLM parameters.
- [ ] Generated code never executes on the host.
- [ ] Public events do not expose hidden reasoning.
- [ ] `RLMFactory` remains the only module constructing `dspy.RLM`.

## 5. FastAPI SSE review

- [ ] `POST /api/chat` remains the canonical transcript path.
- [ ] The route is thin and delegates to `TurnCoordinator`.
- [ ] The response is a typed `AsyncIterable` through `EventSourceResponse`.
- [ ] The stream has cooperative await points.
- [ ] Upstream iterators and leases close in `finally`.
- [ ] Disconnect, explicit cancel, timeout, and budget exhaustion converge on one terminal state.
- [ ] SSE ordering and schema version are contract-tested.
- [ ] OpenAPI changes are intentional and synchronized.

## 6. Daytona Sandbox and Volume review

- [ ] Daytona SDK use remains inside `fleet_rlm.daytona`.
- [ ] Generated Python and approved scripts execute only in Daytona.
- [ ] Sandbox lifecycle decisions are capability-aware.
- [ ] Correctness does not rely on in-memory pause preservation.
- [ ] Volume identity and mount path are validated.
- [ ] Durable content survives Sandbox replacement.
- [ ] Unique run and staging paths prevent concurrent canonical writes.
- [ ] Database version and checksum coordinate canonical promotion.
- [ ] Lease release does not imply Sandbox deletion.
- [ ] Provider errors are normalized before crossing the package boundary.

## 7. State and persistence review

- [ ] Session, turn, run, and checkpoint transitions are explicit.
- [ ] Completed user/assistant exchanges enter History once.
- [ ] Failed or cancelled runs do not advance the successful checkpoint.
- [ ] Idempotency prevents duplicate committed turns.
- [ ] Optimistic concurrency rejects stale checkpoint writes.
- [ ] Artifact references commit with their owning turn.
- [ ] Sandbox and Volume references are tenant/workspace scoped.
- [ ] External exporter failure cannot fail the chat transaction.
- [ ] FastAPI restart recovery is tested.
- [ ] Sandbox-loss recovery is tested when claimed.

## 8. Capability review

### Skills

- [ ] Visibility is deterministic before model ranking.
- [ ] The RLM receives bounded SkillCards, not every Skill body.
- [ ] `load_skill` and resource reads recheck authorization.
- [ ] Skill paths are relative and normalized.
- [ ] Skill version and trust are recorded.
- [ ] Generated Skill candidates cannot become trusted automatically.

### Attachments

- [ ] Requests carry attachment IDs, not host paths.
- [ ] Ownership is rechecked at staging and read time.
- [ ] Staging paths are run-scoped.
- [ ] Private metadata is absent from SSE.

### Artifacts

- [ ] Artifact path comes from Fleet, not model input.
- [ ] Content type and size are bounded.
- [ ] Checksum and provenance are recorded.
- [ ] Volume and database state cannot diverge silently.
- [ ] Retrieval enforces tenant/workspace/session policy.

## 9. Security blockers

Request changes immediately when:

- generated code executes on the host;
- a model-provided path escapes an authorized root;
- one workspace can resolve another workspace's object;
- raw provider errors reach the client;
- credentials or DSNs enter events or model context;
- the RLM can override identity, scope, model role, or budget;
- an untrusted Skill script executes;
- child work writes directly to canonical shared files;
- a public event exposes hidden reasoning;
- approval is bypassed for a memory or Skill mutation.

## 10. Concurrency and lifecycle review

- [ ] Session mutation-lock scope is correct.
- [ ] RLM instance and budget ledger are not shared.
- [ ] Daytona interpreter lease ownership is clear.
- [ ] Cleanup handles partial acquisition.
- [ ] Stop/start, pause/resume, archive/restore, and recreation are not conflated.
- [ ] Lifecycle claims are tested for the configured runner class.
- [ ] Volume last-write-wins behavior is accounted for.
- [ ] Background idle transitions cannot race an active lease.
- [ ] Cancellation does not orphan a committed-but-unreported turn.

## 11. Observability review

- [ ] Run, session, and trace identifiers correlate.
- [ ] Root and sub-model usage are separated.
- [ ] RLM iteration and subquery counts are recorded.
- [ ] Sandbox, interpreter, and Volume references are recorded safely.
- [ ] Skill, attachment, and artifact operations are observable.
- [ ] Terminal status and duration are recorded.
- [ ] Optional exporter failure is non-fatal.
- [ ] Metrics omit private prompt and file content by default.

## 12. Test review

Every implementation ticket requires:

- focused unit tests;
- contract tests for public or SDK boundaries;
- integration tests for cross-package behavior;
- live tests when the claim depends on a real external service.

Reject tests that:

- only assert mocks were called;
- duplicate implementation details without behavior assertions;
- mark a live criterion complete through a fake;
- leave nondeterministic external state behind;
- depend on test order;
- omit failure and cleanup behavior.

## 13. Documentation review

- [ ] Owning phase status reflects actual evidence.
- [ ] `to-spec.md` is updated for requirement changes.
- [ ] `codebase-design.md` is updated for ownership/interface changes.
- [ ] `to-tickets.md` reflects ticket completion and successor work.
- [ ] `context7-contracts.md` is refreshed for dependency-contract changes.
- [ ] Historical commits are not presented as proof of current behavior.
- [ ] Unknown evidence remains unchecked.

## 14. Review output template

```markdown
## Review result

Disposition: approve | comment | request changes

### Blockers

- [B1] Finding
  - Owner:
  - Evidence:
  - Required correction:
  - Required validation:

### Major findings

- [M1] Finding
  - Owner:
  - Evidence:
  - Required correction:

### Minor findings

- [m1] Finding

### Verified claims

- Claim:
  - Evidence:

### Commands reviewed

- `command` -> result

### Status impact

- Phase remains/changes to:
- Acceptance items reopened:
```

## 15. Automatic rejection conditions

1. An API route constructs `dspy.RLM` or calls Daytona directly.
2. Generated code executes outside Daytona.
3. A mutable RLM instance is shared.
4. A canonical Volume path can race without coordination.
5. A failed run advances session History.
6. A public error contains a raw provider exception.
7. A full Skill loads before authorization or need.
8. A live claim is supported only by mocks.
9. A candidate activates without explicit approval.
10. A plan claims completion without exact commit and evidence.
