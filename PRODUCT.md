# Fleet RLM Product

Fleet RLM is a backend-first recursive language-model workbench for long-running
operator tasks. It couples FastAPI HTTP/SSE orchestration with native
`dspy.RLM` execution. The maintained interactive client is the pi-tui terminal
workspace; this repository does not maintain a graphical Web frontend.

Canonical Run Environment set: `deno`, `daytona`.

## What operators can do

- Create Sessions and submit idempotent Turns over the local HTTP/SSE API.
- Attach files, select bundled Skills, and observe bounded Run evidence in the
  terminal timeline.
- Use a Deno profile for local, real-LM RLM execution with Attachment reads and
  Skills.
- Use a Daytona profile for Sandbox-backed execution with Workspace Volume
  storage, Session Workspace files, durable Attachments, and committed
  Artifacts.
- Download committed Artifacts with content-length and SHA-256 verification.

## Product boundaries

Fleet has a deterministic process-local User and Workspace scope. It does not
offer multi-user authentication, caller-provided execution profiles, a public
provider-key API, or a general-purpose Sandbox filesystem browser. Provider
credentials stay in process environment or `.env` values referenced by the
selected policy; they are never returned by the API.

Turns are durable only after `TurnLifecycle.finish()` successfully commits their
validated result. A failed Turn does not advance Session history or publish an
Artifact. Runtime evidence is delivered as typed Runtime Events projected over
SSE; it is separate from engineering-only MLflow tracing.

## Operating model

Select `FLEET_CONFIG_PROFILE` before startup. The committed policy defines the
runtime, model roles, and the names of external configuration values. Deno is
the reduced local environment; Daytona is the full durable environment and
requires a migrated database, a Daytona credential, Databricks AI Gateway
credentials, and its configured gateway URL.

See the [configuration reference](docs/reference/configuration.md),
[architecture](docs/architecture.md), [HTTP API reference](docs/reference/http-api.md),
and [terminal guide](docs/how-to-guides/terminal-tui.md) for operational details.
