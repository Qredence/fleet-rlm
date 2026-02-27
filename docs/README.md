# fleet-rlm Documentation

`fleet-rlm` provides secure, cloud-sandboxed recursive language model workflows with a Web UI, API, and MCP server.

This documentation is for both:

- users operating `fleet-rlm` locally or in deployment workflows
- contributors building integrations, extending runtime behavior, or maintaining the codebase

## Quickstart

```bash
uv tool install fleet-rlm
fleet web
```

Then open `http://localhost:8000`.

Next steps:

- [Installation details](how-to-guides/installation.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [Deploying the API server](how-to-guides/deploying-server.md)

## Choose Your Path

## Use the product

- [Installation](how-to-guides/installation.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [Deploying the API server](how-to-guides/deploying-server.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)

## Contribute to the project

- [Contributing guide](contributing.md)

## Build integrations

- [HTTP and WebSocket API](reference/http-api.md)
- [Python API](reference/python-api.md)
- [CLI reference](reference/cli.md)
- [Using the MCP server](how-to-guides/using-mcp-server.md)

## Understand architecture

- [Architecture overview](architecture.md)
- [Concepts](explanation/concepts.md)
- [User interaction flows](explanation/user-flows.md)
- [Component UML](explanation/component-uml.md)

## Documentation Map (Diataxis)

- [Tutorials](tutorials/index.md)
- [How-to Guides](how-to-guides/index.md)
- [Reference](reference/index.md)
- [Explanation](explanation/index.md)

## Source-of-Truth Policy

When docs conflict with implementation, treat these as authoritative:

- CLI truth: `fleet-rlm --help`
- API truth: `openapi.yaml`
- WebSocket truth: `src/fleet_rlm/server/routers/ws/api.py`

## Archive Note

Historical docs are archived and non-operational:

- [plans/archive/docs-legacy](../plans/archive/docs-legacy/README.md)
