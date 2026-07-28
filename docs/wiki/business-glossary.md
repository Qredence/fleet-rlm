<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: business_term
  name: Business Glossary
  category: business_term
  scope:
      - '**'
-->


### Turn
- Definition：A single request-response cycle in the RLM backend that validates deterministic local scope, processes Attachments and Skill selections, executes through SSE streaming, and commits results atomically. Each Turn creates a fresh dspy.RLM instance and maintains isolation through Interpreter Lease management.
- Aliases：turn-request、rlm-turn

### RLM
- Definition：Recursive Language Model - the core execution engine built around dspy.RLM that handles model calls, tool execution, and iterative reasoning within bounded Turn contexts. Each Turn constructs a fresh RLM instance with isolated state.
- Aliases：recursive-language-model、dspy-rlm

### Daytona
- Definition：Sandboxed execution environment providing isolated interpreter leases, workspace volumes, and artifact promotion for secure Turn execution. Supports both native Python and Deno/Pyodide interpreters with workspace-scoped durable storage.
- Aliases：daytona-sandbox、sandbox-runtime

### Artifact Candidate
- Definition：Temporary file representation during Turn execution that remains private until byte promotion and atomic Turn Commit succeed. Candidates are staged in temporary locations and promoted to durable storage only upon successful completion.
- Aliases：artifact-candidate、staging-artifact

### Deno Contract
- Definition：Deterministic test suite that validates the complete backend execution path using pinned Deno/Pyodide interpreter without network access or external dependencies. Ensures reproducible behavior across environments.
- Aliases：deno-contract-test、deterministic-contract

### Quality Gate
- Definition：Comprehensive validation process executed by 'make check' that includes Python lint/format/type checks, isolated non-live test suites, OpenAPI/TUI type drift verification, pi-tui checks, codebase boundaries, and documentation/harness validation.
- Aliases：quality-gate、make-check

### FLEET_* Configuration
- Definition：Environment variable-based configuration system using FLEET_* prefix for all runtime settings. Canonical public environments include 'deno' and 'daytona' profiles with explicit opt-in for live credential usage.
- Aliases：fleet-config、environment-variables

### OpenAPI Contract
- Definition：Generated HTTP API specification maintained through 'make api-sync' that produces both openapi.yaml and tools/fleet-tui/src/generated/openapi.ts. Any generated diff must be reviewed as a public contract change.
- Aliases：openapi-spec、api-contract
