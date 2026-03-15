# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for Fleet-RLM.

## What is an ADR?

An ADR is a document that captures an important architectural decision along with its context and consequences. ADRs help future contributors understand why the system is designed the way it is.

## ADR Format

Each ADR follows this structure:

- **Status**: Current state (Proposed, Accepted, Deprecated, Superseded)
- **Context**: The problem and constraints that led to the decision
- **Decision**: The architectural choice made
- **Consequences**: The results of the decision (positive, negative, and neutral)

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [001](./001-rlm-runtime-architecture.md) | RLM Runtime Architecture | Accepted |
| [002](./002-modal-sandbox-execution.md) | Modal Sandbox Execution Model | Accepted |
| [003](./003-neon-postgres-rls-persistence.md) | Neon/Postgres with RLS for Persistence | Accepted |
| [004](./004-dual-auth-modes.md) | Dual Authentication Modes (Dev/Entra) | Accepted |

## Creating a New ADR

1. Copy the template below to a new file named `NNN-short-title.md` (sequential numbering)
2. Fill in all sections
3. Update this index

### Template

```markdown
# ADR-NNN: Title

## Status

[Proposed|Accepted|Deprecated|Superseded]

## Context

[Describe the situation, problem, and constraints]

## Decision

[Describe the architectural choice made]

## Consequences

### Positive

[Benefits of the decision]

### Negative

[Drawbacks and trade-offs]

### Neutral

[Side effects that are neither good nor bad]

## References

- [Link to relevant source files or documentation]
```

## Further Reading

- [Documenting Architecture Decisions (Michael Nygard)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- [ADR GitHub Organization](https://adr.github.io/)
