# Using the MCP Server

The Model Context Protocol (MCP) lets AI clients call `fleet-rlm` tools through `serve-mcp`.

> **Note:** MCP support is **work in progress** and **not recommended** for production use.

## Quick Start

```bash
# Add the optional MCP extra to your uv project
uv add "fleet-rlm[mcp]"

# Start the MCP server
uv run fleet-rlm serve-mcp
```

By default, the MCP server runs with `stdio` transport for local tool integration.

## Command Options

```text
Usage: fleet-rlm serve-mcp [OPTIONS]

Run optional FastMCP server surface (requires the MCP extra).

Options:
  --transport TEXT     FastMCP transport: stdio, sse, streamable-http
                       [default: stdio]
  --host TEXT          Host for HTTP transports [default: 127.0.0.1]
  --port INTEGER       Port for HTTP transports [default: 8001]
  --help               Show this message and exit.
```

### Transport Options

| Transport | Description |
|-----------|-------------|
| `stdio` | Standard input/output transport (default). Best for local CLI integration and Claude Desktop. |
| `sse` | Server-Sent Events transport. Runs an HTTP server on the specified host and port. |
| `streamable-http` | Streaming HTTP transport. Runs an HTTP server on the specified host and port. |

### HTTP Transport Defaults

When using `sse` or `streamable-http` transports:
- **Host:** `127.0.0.1` (localhost only by default)
- **Port:** `8001`

Example with HTTP transport:

```bash
uv run fleet-rlm serve-mcp --transport sse --host 0.0.0.0 --port 8001
```

## Configure Claude Desktop

Add to your MCP config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fleet-rlm": {
      "command": "uv",
      "args": ["run", "fleet-rlm", "serve-mcp", "--transport", "stdio"],
      "env": {
        "DSPY_LM_MODEL": "openai/gpt-4o-mini",
        "DSPY_LLM_API_KEY": "sk-..."
      }
    }
  }
}
```

## Hydra Overrides

You can pass Hydra overrides at startup to customize runtime behavior:

```bash
uv run fleet-rlm serve-mcp --transport stdio \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn
```

Common overrides include:
- `interpreter.async_execute=true|false` - Enable async tool execution
- `agent.guardrail_mode=off|warn|strict` - Set output guardrail behavior
- `agent.max_output_chars=10000` - Limit output length

## Tools Exposed by MCP

The MCP server exposes the following tools from
`src/fleet_rlm/integrations/mcp/server.py`:

| Tool | Description |
|------|-------------|
| `chat_turn` | Single ReAct turn for chat-style interaction |
| `analyze_long_document` | Long-context analysis of documents |
| `summarize_long_document` | Long-context summarization of documents |
| `grounded_answer` | Chunked answer with citations |
| `triage_incident_logs` | Incident/log triage workflow |
| `memory_tree` | Bounded memory/volume tree inspection |
| `memory_structure_audit` | Memory layout audit recommendations |
| `clarification_questions` | Generate safe clarifying questions for risky operations |

## Prerequisites

Ensure MCP dependencies are installed.

For a package install, add the MCP extra:

```bash
uv add "fleet-rlm[mcp]"
```

From a source checkout, sync the repo extras instead:

```bash
uv sync --extra mcp
```

## Troubleshooting

1. **Check client logs** (for Claude Desktop: `~/Library/Logs/Claude/mcp.log`)

2. **Verify MCP dependencies** are installed:
   ```bash
   uv add "fleet-rlm[mcp]"
   ```

3. **Test server launch locally**:
   ```bash
   uv run fleet-rlm serve-mcp --transport stdio
   ```

4. **For HTTP transport issues**, verify the host and port are accessible:
   ```bash
   # Test SSE transport
   curl http://127.0.0.1:8001/sse
   ```

## Related Documentation

- [Installation Guide](installation.md) - Setting up fleet-rlm
- [Runtime Settings](runtime-settings.md) - Configuring model and runtime behavior
