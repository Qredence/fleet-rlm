# fleet-rlm Documentation

Welcome to the documentation for **fleet-rlm**, a Python package implementing **Recursive Language Models (RLM)** with DSPy and Modal for secure, cloud-based code execution.

## Overview

Recursive Language Models (RLM) represent an inference strategy where:

- LLMs treat long contexts as an **external environment** rather than direct input.
- The model writes Python code to programmatically explore data.
- Code executes in a sandboxed environment (Modal cloud).
- Only relevant snippets are sent to sub-LLMs for semantic analysis.

This project is based on research from the [Recursive Language Models paper](https://arxiv.org/abs/2501.123) by Zhang, Kraska, and Khattab (2025).

## Key Features

- **Secure Cloud Execution**: Code runs in Modal's isolated sandbox environment.
- **DSPy Integration**: Built on DSPy 3.1.3 with custom signatures for RLM tasks.
- **CLI Interface**: Typer-based CLI with multiple demo commands.
- **Extensible Tools**: Support for custom tools that bridge sandbox and host.
- **Secret Management**: Secure handling of API keys via Modal secrets.

## Getting Started

Check out the [Getting Started guide](getting-started.md) to set up your environment and run your first RLM agent.

## Documentation Contents

- **[Getting Started](getting-started.md)**: Installation, Environment Setup, and Modal Configuration.
- **[Core Concepts](concepts.md)**: Theory behind RLM, Architecture, and Design Patterns.
- **Tutorials**:
  - **[Basic Usage](tutorials/basic-usage.md)**: Running the Fibonacci demo.
  - **[Document Analysis](tutorials/doc-analysis.md)**: Extracting information from long documents.
- **Guides**:
  - **[CLI Reference](guides/cli-reference.md)**: Detailed command documentation.
  - **[Jupyter Notebooks](guides/notebooks.md)**: Using the provided notebooks.
  - **[Troubleshooting](guides/troubleshooting.md)**: Common issues and fixes.
- **[Contributing](contributing.md)**: How to contribute to the project.

## License

This project is licensed under the [MIT License](../LICENSE).
