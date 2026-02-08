# CLI Reference

The `fleet-rlm` application uses [Typer](https://typer.tiangolo.com/) to provide a rich CLI experience.

## General Usage

```bash
uv run fleet-rlm [COMMAND] [OPTIONS]
```

## Commands

| Command              | Description                                                    | Key Options                                                             |
| :------------------- | :------------------------------------------------------------- | :---------------------------------------------------------------------- |
| `run-basic`          | Run a simple Q&A task with code generation.                    | `--question`: The prompt<br>`--volume-name`: Optional persistent volume |
| `run-architecture`   | Extract architecture/concepts from a document.                 | `--docs-path`: Path to text file<br>`--query`: Info to extract          |
| `run-api-endpoints`  | Extract API endpoints using parallel batching.                 | `--docs-path`: Path to text file                                        |
| `run-error-patterns` | Analyze error patterns in documentation.                       | `--docs-path`: Path to text file                                        |
| `run-trajectory`     | Inspect the full execution history (trajectory) of an RLM run. | `--docs-path`: Path to text file<br>`--chars`: Limit input size         |
| `run-custom-tool`    | Demonstrate usage of custom tools (e.g., Regex).               | `--docs-path`: Path to text file                                        |
| `check-secret`       | Check if Modal secrets are correctly configured.               |                                                                         |
| `check-secret-key`   | Inspect a specific secret key value.                           | `--key`: The env var name to check                                      |

## Common Options

### `--volume-name`

All `run-*` commands support this option.

- **Usage**: `--volume-name my-data-vol`
- **Effect**: Mounts the specified Modal Volume to `/data/` inside the sandbox.
- **Requirement**: The volume must be created first via `modal volume create`.

### `--docs-path`

Required for analysis commands.

- **Usage**: `--docs-path path/to/file.txt`
- **Effect**: Reads the local file and uploads/makes it available to the sandbox context.

## Help

For any command, you can append `--help` to see the full list of arguments and options.

```bash
uv run fleet-rlm run-architecture --help
```
