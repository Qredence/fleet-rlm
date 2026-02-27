# Using the MCP Server

The Model Context Protocol (MCP) lets AI clients call `fleet-rlm` tools through `serve-mcp`.

{% hint style="warning" %}
MCP support is **work in progress** and **not recommended** for production use.
{% endhint %}

## Configure Claude Desktop

Add to your MCP config file:

* macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
* Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

You can pass Hydra overrides at startup:

```bash
uv run fleet-rlm serve-mcp --transport stdio \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn
```

## Tools Exposed by MCP

Current tool surface from `src/fleet_rlm/mcp/server.py`:

* `chat_turn`: single ReAct turn for chat-style interaction
* `analyze_long_document`: long-context analysis
* `summarize_long_document`: long-context summarization
* `grounded_answer`: chunked answer with citations
* `triage_incident_logs`: incident/log triage workflow
* `memory_tree`: bounded memory/volume tree inspection
* `memory_structure_audit`: memory layout audit recommendations
* `clarification_questions`: generate safe clarifying questions for risky operations

## Troubleshooting

* Check client logs (for Claude Desktop: `~/Library/Logs/Claude/mcp.log`).
* Confirm MCP dependencies are installed: `uv sync --extra dev --extra mcp`.
* Validate server launch locally:

```bash
uv run fleet-rlm serve-mcp --transport stdio
```
