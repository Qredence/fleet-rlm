# Using the MCP Server

The **Model Context Protocol (MCP)** allows you to connect `fleet-rlm` directly to AI assistants like **Claude Desktop** or VS Code. This enables Claude to run RLM tasks (executing code in Modal) as if they were native tools.

## What is MCP?

MCP provides a standard way for AI models to discover and call extenral tools. `fleet-rlm` exposes its core long-context and code-execution capabilities as MCP tools.

## Configuration for Claude Desktop

To use `fleet-rlm` with Claude Desktop, add the following to your config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fleet-rlm": {
      "command": "uv",
      "args": ["run", "fleet-rlm", "serve-mcp", "--transport", "stdio"],
      "env": {
        "DSPY_LM_MODEL": "openai/gpt-4o",
        "DSPY_LLM_API_KEY": "sk-..."
      }
    }
  }
}
```

## Available Tools

Once connected, Claude will have access to:

### `chat_turn`

Executes a single turn of the RLM ReAct loop.

- **Use case**: "Write Python code to calculate the orbital period of Mars."

### `analyze_long_document`

Performs a deep scan of a document to extract specific answers.

- **Use case**: "Read `report.pdf` and find all mentions of 'Q3 Revenue'."

### `summarize_long_document`

Generates a comprehensive summary of a long text.

- **Use case**: "Summarize the key points of `transcript.txt`."

## Troubleshooting

- **Logs**: Claude Desktop logs are found in `~/Library/Logs/Claude/mcp.log`.
- **Latency**: RLM tasks can take 30-60 seconds. Ensure your client doesn't time out.
