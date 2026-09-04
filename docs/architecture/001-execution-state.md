# ADR 001: Execution-state vocabulary

**Status:** accepted
**Decision date:** 2026-09-04

Fleet uses the following terms as named ownership boundaries:

| Term | Meaning | Owner / lifetime |
| --- | --- | --- |
| **Workspace** | Durable tenant scope for Sessions, files, attachments, artifacts, and policy-authorized state. | Durable Database + Volume scope. |
| **Session** | Durable ordered conversation in one Workspace. | Database; owns committed Turns. |
| **Turn** | One durable conversational result: accepted user input and its settled outcome. | Database; committed atomically by the Turn lifecycle. |
| **Run** | One execution attempt for a Turn. A retry/recovery attempt is a new Run, not a new Turn. | Claimed, deadline-bounded lifecycle. |
| **RunClaim** | Durable, compare-and-set authority to perform one Run's useful work and settlement. | Persistence claim policy. |
| **SessionSandbox** | A reusable Daytona Sandbox scoped to one Workspace + Session. | Session environment owner; never the durable Session authority. |
| **InterpreterContext** | The live Python namespace and caller-owned interpreter bindings used by an execution. | Ephemeral; bounded by its environment lifecycle. |
| **ChildEnvironment** | Fresh, isolated execution resources for one recursive child Run. | Child runtime owner; deleted after child cleanup. |

A **Run is an execution attempt**. A **Turn is the durable conversational
result**. They must never be used as synonyms. A SessionSandbox is an execution
resource, not a Session, and an InterpreterContext is not durable Session state.

## Consequences

- Transport adapters refer to `Run` for live execution and `Turn` for durable
  history/settlement.
- Comments and identifiers use `SessionSandbox` for the persistent-in-process
  root sandbox concept, `InterpreterContext` for Python state, and
  `ChildEnvironment` for recursive child resources.
- Historical class names may remain during migration, but their docs must name
  the canonical boundary and the migration ADR must name their deletion phase.
