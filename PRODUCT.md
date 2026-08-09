# Fleet RLM Product

Fleet RLM is a backend-first recursive language-model workbench for long-running
operator tasks. It couples FastAPI HTTP/SSE orchestration with native
`dspy.RLM` execution. The maintained interactive client is the pi-tui terminal
workspace; this repository does not maintain a graphical Web frontend.

Canonical Run Environment set: `daytona`.

## What operators can do

- Create Sessions and submit idempotent Turns over the local HTTP/SSE API.
- Attach files, select bundled Skills, and observe bounded Run evidence in the
  terminal timeline.
- In the Daytona environment, inspect the bounded, read-only logical Workspace
  Volume tree from the API or terminal client.
- Use a Daytona profile for Sandbox-backed execution with Workspace Volume
  storage, Session Workspace files, bounded Workspace Memory recall, durable
  Attachments, and committed Artifacts.
- Download committed Artifacts with content-length and SHA-256 verification.

## Product boundaries

Fleet has a deterministic process-local User and Workspace scope. It does not
offer multi-user authentication, caller-provided execution profiles, a public
provider-key API, or a general-purpose Sandbox filesystem browser. Provider
credentials stay in process environment or `.env` values referenced by the
selected policy; they are never returned by the API.

The Volume tree is a bounded read-only logical view of relative paths; it is not
a general-purpose filesystem browser and does not expose provider paths or file
contents. Daytona Workspace Memory is immediate workspace-wide state in
`memory/MEMORIES.md`: each Turn receives a bounded newest-record digest in
`session_context`, while the RLM may read or append through host-mediated Tools.
It may append only when the user explicitly asks to remember something. Memory
is distinct from Session History, survives failed or cancelled Runs and Sandbox
replacement, and is not a Turn-commit record.

Turns are durable only after `TurnLifecycle.finish()` successfully commits their
validated result. A failed Turn does not advance Session history or publish an
Artifact. Workspace Memory appends become durable independently of Turn Commit
and survive failed or cancelled Runs and Sandbox replacement. Runtime evidence
is delivered as typed Runtime Events projected over SSE; it is separate from
engineering-only MLflow tracing.

## Operating model

Select `[config] default_profile` in `config/fleet.toml`, directly or through
the TUI `/profiles` command, then restart Fleet. The committed policy defines
the runtime, model roles, token limits, provider route, and names of external
configuration values. The shipped `daytona-recursive` and `daytona` profiles use
OpenCode Go; managed and benchmark profiles use the Databricks AI Gateway. See
the [profile matrix](docs/reference/profile-matrix.md) before selecting a
profile. Daytona requires a migrated database, a Daytona credential, and the
provider values named by the selected policy. The current live proof does not
yet establish Workspace Memory across real provider-backed Sandbox replacement
and separate Sessions.

See the [configuration reference](docs/reference/configuration.md),
[architecture](docs/architecture.md), [HTTP API reference](docs/reference/http-api.md),
and [terminal guide](docs/how-to-guides/terminal-tui.md) for operational details.
