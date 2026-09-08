# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.7.x   | Current maintenance line |
| < 0.7   | Upgrade to the current line |

The source version is defined in `pyproject.toml`. This table identifies the
maintenance line, not a claim that a particular checkout has passed a security
audit. See [CONTRIBUTING.md](CONTRIBUTING.md) for the validation lanes.

Fleet's API has deterministic local scope and no caller authentication. Use
the default loopback binding unless deployment access controls are configured.
The settings API remains loopback-only. Report sanitized reproduction steps;
never include credentials, `.env` values, private trace content, or raw provider
errors in a public report.

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
