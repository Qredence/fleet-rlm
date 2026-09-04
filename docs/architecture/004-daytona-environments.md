# ADR 004: Daytona environments

**Status:** accepted
**Decision date:** 2026-09-04

Daytona policy is described with three independent concepts:

| Term | Meaning |
| --- | --- |
| **SnapshotDefinition** | Immutable image identity, Python/runtime dependencies, and resource contract used to create a Sandbox. |
| **SandboxProfile** | Purpose-specific resource and isolation behavior selected from a SnapshotDefinition. |
| **WarmPoolPolicy** | Bounded policy for pre-created or retained Sandboxes; it never changes durable Session correctness. |

Fleet has three SandboxProfiles:

1. **SessionSandbox** — a root execution Sandbox scoped to one Workspace and
   Session; it may be retained/reused only after a clean settled Turn.
2. **ChildEnvironment** — an isolated recursive-child Sandbox with a
   child-scoped Volume path; it is deleted and absence-confirmed after child
   cleanup.
3. **BenchmarkSandbox** — an ephemeral, isolated measurement Sandbox with no
   conversational ownership; it is deleted after the benchmark sample.

SessionSandboxes and ChildEnvironments intentionally have different lifecycle
policies. A SessionSandbox may retain useful ephemeral interpreter state across
eligible sequential Runs, while a ChildEnvironment is evidence-only and must
not leak state into a sibling, parent, or later Turn. Neither policy changes
what is durable: Database and Volume state remain authoritative.
