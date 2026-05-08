# VSCode Configuration for ty Type Checker

## Overview

This VSCode workspace is configured to use **ty**, an extremely fast Python type checker, for static type analysis. ty is a strict type checker that provides fast feedback on type errors in your Python code.

## ty Version

- **ty version**: 0.0.34
- **Configuration file**: `pyproject.toml` → `[tool.ty.src]`

## Configuration Details

### 1. **settings.json**

The following settings have been configured:

- `python.analysis.typeCheckingMode: "standard"` — Keeps baseline Python extension type checking enabled alongside ty
- `python.linting.enabled: true` — Enables linting for comprehensive feedback
- `python.testing.pytestEnabled: true` — Enables pytest for test discovery
- `files.exclude` — Hides ty cache files (`.ty_cache`) from the explorer

### 2. **tasks.json**

Three tasks are configured for ty integration:

#### `ty: Check types`
- **Command**: `ty check`
- **Shortcut**: Available via Command Palette (`Cmd+Shift+P` → "ty: Check types")
- **Behavior**: Runs a single check and displays results in the terminal
- **Problem Matcher**: Parses ty output to show type errors inline in the editor

#### `ty: Watch types`
- **Command**: `ty check --watch`
- **Behavior**: Continuously monitors files and re-runs checks on save
- **Use Case**: Keep this running in a background terminal for real-time feedback
- **Panel**: Opens in a new terminal panel by default

#### `ty: Explain rules`
- **Command**: `ty explain`
- **Behavior**: Displays ty's rule documentation
- **Use Case**: Learn about specific type rules

### 3. **keybindings.json**

- **Ctrl+Shift+T** (Mac/Linux) or **Ctrl+Shift+T** (Windows) — Runs "ty: Check types"
- **Context**: Only active when editing Python files

### 4. **launch.json**

Standard Python debugging configurations with one FastAPI-specific launch target:

- `Python: FastAPI` — Launches the development server (`fleet-rlm serve-api`) with debugging enabled

## Usage

### Run Type Checks

1. **Single check**: Press `Ctrl+Shift+T` or run the "ty: Check types" task
2. **Watch mode**: Run the "ty: Watch types" task to monitor changes continuously
3. **Terminal**: `ty check` from the project root

### Configure ty

Edit `pyproject.toml`:

```toml
[tool.ty.src]
exclude = [
    "tests/",
    "scripts/",
    "notebooks/",
    "test_modal_guard.py",
    "setup.py",
]
```

### View ty Rules

```bash
ty explain
```

## Limitations and Notes

### ⚠️ VSCode Native Support

- **VSCode's Python extension** does not natively support ty as a type checker option
- Workaround: Configuration uses **tasks** and **problem matchers** to parse ty's CLI output
- ty's language server (`ty server`) exists but is not wired into VSCode's default Python extension configuration
- Future: Once VSCode Python extension adds native ty support, this can be simplified

### ✅ Current Integration Level

- ✅ Task-based execution (single checks and watch mode)
- ✅ Problem matcher for inline error display
- ✅ Keyboard shortcuts for quick access
- ✅ Cache exclusion for cleaner UI
- ✅ Debug launch configuration
- ❌ Real-time LSP-based diagnostics (requires VSCode Python extension update or external LSP client extension)

### Recommended Setup

1. **Install the ty LSP client (optional)**:
   - Use the "Pylance" extension alongside ty tasks for dual type checking
   - Or use a generic LSP Client extension that can connect to `ty server`

2. **Best Practice**: Run "ty: Watch types" in a background terminal while developing:
   ```bash
   ty check --watch
   ```

## Commands for Development

```bash
# Single type check
make typecheck                # Runs: uv run ty check src

# Watch mode
ty check --watch

# View rule explanations
ty explain

# Check specific file
ty check src/fleet_rlm/api/main.py
```

## Integration with Development Workflow

- **Pre-commit**: ty checks can be added to pre-commit hooks (see `.pre-commit-config.yaml`)
- **CI/CD**: GitHub Actions runs `make typecheck` on PR/push
- **Local**: Use "ty: Watch types" task for continuous feedback during development

## Troubleshooting

### ty command not found

Ensure the virtual environment is active:

```bash
uv sync --all-extras --dev
```

### Problem matcher not capturing errors

1. Verify ty is executable: `which ty`
2. Check the terminal output for error format
3. Adjust the regex pattern in `tasks.json` if ty output format has changed

### Too many false positives

Review ty's configuration in `pyproject.toml`:

```bash
ty explain  # View all rules
```

Adjust `[tool.ty.src]` exclusions if needed.

## References

- **ty Official**: https://github.com/pytypes/ty
- **VSCode Tasks**: https://code.visualstudio.com/docs/editor/tasks
- **Problem Matchers**: https://code.visualstudio.com/docs/editor/tasks#_defining-a-problem-matcher
