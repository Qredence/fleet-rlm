# fleet-rlm Documentation

Welcome to **fleet-rlm**, an agent framework for running Recursive Language Models (RLM) in secure Modal sandboxes, featuring a local Web UI for interactive chat and deep task execution.

## Documentation Structure

Our documentation follows the [Diátaxis](https://diataxis.fr/) framework:

### 🎓 [Tutorials](tutorials/index.md)

_Learning-oriented lessons to get you started._

- [Basic Usage](tutorials/01-basic-usage.md) - Run your first agent.
- [Document Analysis](tutorials/02-doc-analysis.md) - Extract data from files.
- [Interactive Chat](tutorials/03-interactive-chat.md) - Use the `fleet web` React UI, or `fleet` (Ink/Python CLI).

### 🗺️ [How-to Guides](how-to-guides/index.md)

_Problem-oriented steps for specific goals._

- [Installation](how-to-guides/installation.md)
- [Configuring Modal (Secrets & Volumes)](how-to-guides/configuring-modal.md)
- [Managing Skills](how-to-guides/managing-skills.md)
- [Deploying the API Server & Web UI](how-to-guides/deploying-server.md)
- [Using with Claude Desktop (MCP)](how-to-guides/using-mcp-server.md)
- [Using with Claude Code (Skills/Agents/Teams)](how-to-guides/using-claude-code-agents.md)
- [Performance Regression Guardrail](how-to-guides/performance-regression-guardrail.md)

### 📖 [Reference](reference/index.md)

_Information-oriented technical descriptions._

- [CLI Commands](reference/cli.md)
- [Python API](reference/python-api.md)
- [HTTP API](reference/http-api.md)
- [Database Architecture](db.md)
- [Auth Modes](auth.md)
- [Sandbox File System](reference/sandbox-fs.md)
- [Source Layout](reference/source-layout.md)

### 🧠 [Explanation](explanation/index.md)

_Understanding-oriented background and concepts._

- [RLM Concepts](explanation/rlm-concepts.md)
- [Architecture](explanation/architecture.md)
- [Stateful Architecture](explanation/stateful-architecture.md)
- [Memory Topology Notes](explanation/memory-topology.md)

## Key Features

- **Multi-Interface**: Modern React Web UI, plus CLI, TUI, FastAPI, and MCP for Claude.
- **Secure Cloud Execution**: Code and commands run safely in isolated Modal Sandboxes.
- **DSPy Integration**: Powerful programmatic planning and sub-agent generation.
- **Long Contexts**: Process massive files and tasks via RLM recursion.
