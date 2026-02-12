# fleet-rlm Documentation

Welcome to **fleet-rlm**, a Python package for running Recursive Language Models (RLM) in secure Modal sandboxes.

## Documentation Structure

Our documentation follows the [Diátaxis](https://diataxis.fr/) framework:

### 🎓 [Tutorials](tutorials/index.md)

_Learning-oriented lessons to get you started._

- [Basic Usage](tutorials/01-basic-usage.md) - Run your first agent.
- [Document Analysis](tutorials/02-doc-analysis.md) - Extract data from files.
- [Interactive Chat](tutorials/03-interactive-chat.md) - Use the TUI.

### 🗺️ [How-to Guides](how-to-guides/index.md)

_Problem-oriented steps for specific goals._

- [Installation](how-to-guides/installation.md)
- [Configuring Modal (Secrets & Volumes)](how-to-guides/configuring-modal.md)
- [Managing Skills](how-to-guides/managing-skills.md)
- [Deploying the API Server](how-to-guides/deploying-server.md)
- [Using with Claude Desktop (MCP)](how-to-guides/using-mcp-server.md)
- [Using with Claude Code (Skills/Agents/Teams)](how-to-guides/using-claude-code-agents.md)

### 📖 [Reference](reference/index.md)

_Information-oriented technical descriptions._

- [CLI Commands](reference/cli.md)
- [Python API](reference/python-api.md)
- [HTTP API](reference/http-api.md)
- [Sandbox File System](reference/sandbox-fs.md)
- [Source Layout](reference/source-layout.md)

### 🧠 [Explanation](explanation/index.md)

_Understanding-oriented background and concepts._

- [RLM Concepts](explanation/rlm-concepts.md)
- [Architecture](explanation/architecture.md)
- [Stateful Architecture](explanation/stateful-architecture.md)
- [Memory Topology Notes](explanation/memory-topology.md)

## Key Features

- **Secure Cloud Execution**: Hosted on Modal.
- **DSPy Integration**: Powerful programmatic planning.
- **Long Contexts**: Process unlimited text via RLM recursion.
- **Multi-Interface**: CLI, TUI, FastAPI, and MCP for Claude.
