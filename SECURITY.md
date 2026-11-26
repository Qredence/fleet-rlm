# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.14.x  | Current maintenance line |
| < 0.14  | Upgrade to the current line |

`backend/pyproject.toml` defines the source version. This table identifies the
maintenance line, not a claim that a particular checkout has passed a security
audit. See [AGENTS.md](AGENTS.md) and the `check` targets in the
[`Makefile`](Makefile) for the validation lanes.

Fleet's API authenticates callers only when you set `FLEET_API_BEARER_TOKEN`.
When you set it, the session, file, chat, memory, skill, model, and settings
routes under `/api` require an `Authorization: Bearer <token>` header. When you
do not set it, these routes accept all callers. The `/healthz` and `/readyz`
probes never require a token. Set a token before you expose the API outside a
trusted network.

The backend does not limit the settings API to loopback callers. The
`FLEET_CORS_ORIGINS` allowlist controls browser access and defaults to
`http://localhost:5173`. Add only the origins that serve your frontend.

The Daytona, S3, LLM, and Tavily credentials stay on the Fleet host. The backend
does not send them to the Daytona sandbox.

Report sanitized reproduction steps. Never include credentials, `.env` values,
private trace content, or raw provider errors in a public report.

## Reporting a Vulnerability

If you discover a security vulnerability in fleet-rlm, please report it responsibly.

**Email:** [contact@qredence.ai](mailto:contact@qredence.ai)

Please include:

- A description of the vulnerability
- Steps to reproduce the issue
- The potential impact
- Any suggested fixes (optional)

We will acknowledge receipt within **48 hours** and aim to provide an initial assessment within **5 business days**. If the vulnerability is accepted, we will work on a fix and coordinate disclosure. If declined, we will explain our reasoning.

Please do **not** open a public GitHub issue for security vulnerabilities.
