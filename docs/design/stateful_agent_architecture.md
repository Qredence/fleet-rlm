# Stateful Agent Architecture Design

## Overview

This design integrates `dspy.RLM` as the core orchestration layer for stateful agents, using `ModalInterpreter` as the CodeInterpreter backend and Modal Volume for persistent state across sessions.

## Core Design Principles

1. **dspy.RLM as Primary Interface**: Agents use `dspy.RLM` for reasoning and code generation
2. **ModalInterpreter as Backend**: The interpreter provides the `CodeInterpreter` interface for RLM
3. **Volume-Based Persistence**: All state persists to Modal Volume at `/data/workspace/`
4. **Session Continuity**: Agents can retrieve, modify, and improve previous work across sessions

## Architecture Components

### 1. StatefulSandboxManager

Wraps `ModalInterpreter` with session persistence and workspace management.

```python
class StatefulSandboxManager:
    """Manages sandbox lifecycle with persistent workspace state.

    Wraps ModalInterpreter to provide:
    - Session persistence across RLM iterations
    - Workspace state management at /data/workspace/
    - Automatic checkpoint/restore functionality
    """

    def __init__(
        self,
        session_id: str,
        volume_name: str = "rlm-workspace",
        checkpoint_interval: int = 5,  # iterations
        **interpreter_kwargs
    ):
        self.session_id = session_id
        self.volume_name = volume_name
        self.checkpoint_interval = checkpoint_interval
        self._interpreter = ModalInterpreter(
            volume_name=volume_name,
            volume_mount_path="/data",
            **interpreter_kwargs
        )
        self._iteration_count = 0
        self._workspace_initialized = False

    def start(self) -> None:
        """Initialize sandbox and restore previous session state."""
        self._interpreter.start()
        self._initialize_workspace()
        self._restore_session_state()

    def execute(self, code: str, variables: dict | None = None) -> FinalOutput:
        """Execute code with automatic checkpointing."""
        result = self._interpreter.execute(code, variables)
        self._iteration_count += 1

        if self._iteration_count % self.checkpoint_interval == 0:
            self._checkpoint_state()

        return result

    def _initialize_workspace(self) -> None:
        """Create workspace structure in volume."""
        if self._workspace_initialized:
            return

        init_code = f'''
import os
import json

WORKSPACE_ROOT = "/data/workspace"
SESSION_DIR = os.path.join(WORKSPACE_ROOT, "{self.session_id}")
CHECKPOINTS_DIR = os.path.join(SESSION_DIR, "checkpoints")
ARTIFACTS_DIR = os.path.join(SESSION_DIR, "artifacts")
METADATA_FILE = os.path.join(SESSION_DIR, "session.json")

# Create directory structure
for dir_path in [WORKSPACE_ROOT, SESSION_DIR, CHECKPOINTS_DIR, ARTIFACTS_DIR]:
    os.makedirs(dir_path, exist_ok=True)

# Initialize session metadata if not exists
if not os.path.exists(METADATA_FILE):
    metadata = {{
        "session_id": "{self.session_id}",
        "created_at": __import__('time').time(),
        "iteration_count": 0,
        "checkpoints": [],
        "artifacts": []
    }}
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

Final = {{"status": "initialized", "session_dir": SESSION_DIR}}
'''
        self._interpreter.execute(init_code)
        self._workspace_initialized = True

    def _checkpoint_state(self) -> None:
        """Save current sandbox state to volume."""
        checkpoint_code = f'''
import os
import json
import pickle

SESSION_DIR = "/data/workspace/{self.session_id}"
CHECKPOINTS_DIR = os.path.join(SESSION_DIR, "checkpoints")
checkpoint_id = "checkpoint_{{:04d}}".format({self._iteration_count})
checkpoint_path = os.path.join(CHECKPOINTS_DIR, checkpoint_id)

# Save relevant globals (excluding builtins and private)
state = {{k: v for k, v in globals().items()
          if not k.startswith('_') and k not in ('os', 'json', 'pickle')}}

# Serialize state
with open(checkpoint_path + ".pkl", "wb") as f:
    pickle.dump(state, f)

# Update metadata
with open(os.path.join(SESSION_DIR, "session.json"), "r") as f:
    metadata = json.load(f)

metadata["iteration_count"] = {self._iteration_count}
metadata["checkpoints"].append({{
    "id": checkpoint_id,
    "iteration": {self._iteration_count},
    "timestamp": __import__('time').time()
}})

with open(os.path.join(SESSION_DIR, "session.json"), "w") as f:
    json.dump(metadata, f, indent=2)

Final = {{"checkpoint_id": checkpoint_id, "state_keys": list(state.keys())}}
'''
        self._interpreter.execute(checkpoint_code)

    def _restore_session_state(self) -> None:
        """Restore state from previous checkpoint if available."""
        restore_code = f'''
import os
import json

SESSION_DIR = "/data/workspace/{self.session_id}"
METADATA_FILE = os.path.join(SESSION_DIR, "session.json")

if os.path.exists(METADATA_FILE):
    with open(METADATA_FILE, "r") as f:
        metadata = json.load(f)

    iteration_count = metadata.get("iteration_count", 0)
    checkpoints = metadata.get("checkpoints", [])

    if checkpoints:
        latest = checkpoints[-1]
        Final = {{
            "restored": True,
            "iteration_count": iteration_count,
            "latest_checkpoint": latest["id"],
            "total_checkpoints": len(checkpoints)
        }}
    else:
        Final = {{"restored": False, "iteration_count": 0}}
else:
    Final = {{"restored": False, "message": "No previous session found"}}
'''
        result = self._interpreter.execute(restore_code)
        # Update local iteration count from restored state
        if hasattr(result, 'iteration_count'):
            self._iteration_count = result.iteration_count

    def save_artifact(self, name: str, content: str | bytes, metadata: dict | None = None) -> str:
        """Save an artifact to the workspace."""
        # Implementation for saving files, code, etc.
        pass

    def load_artifact(self, name: str) -> dict:
        """Load an artifact from the workspace."""
        pass

    def list_artifacts(self) -> list[dict]:
        """List all artifacts in the workspace."""
        pass

    def shutdown(self) -> None:
        """Final checkpoint and shutdown."""
        self._checkpoint_state()
        self._interpreter.shutdown()
```

### 2. AgentStateManager

High-level interface for agents using `dspy.RLM`.

```python
class AgentStateManager:
    """High-level state management interface for RLM-based agents.

    Provides a simple API for agents to:
    - Store and retrieve state across RLM iterations
    - Manage artifacts (code, documents, results)
    - Track session history and metrics
    """

    def __init__(
        self,
        sandbox_manager: StatefulSandboxManager,
        state_schema: dict[str, type] | None = None
    ):
        self.sandbox = sandbox_manager
        self.state_schema = state_schema or {}
        self._state_cache: dict[str, Any] = {}

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state value from the sandbox."""
        if key in self._state_cache:
            return self._state_cache[key]

        result = self.sandbox.execute(f'Final = globals().get("{key}", {repr(default)})')
        value = result if not hasattr(result, '__dict__') else result.output
        self._state_cache[key] = value
        return value

    def set_state(self, key: str, value: Any) -> None:
        """Set a state value in the sandbox."""
        self.sandbox.execute(f'{key} = {repr(value)}')
        self._state_cache[key] = value

    def update_state(self, updates: dict[str, Any]) -> None:
        """Batch update multiple state values."""
        code = "\n".join(f'{k} = {repr(v)}' for k, v in updates.items())
        self.sandbox.execute(code)
        self._state_cache.update(updates)

    def get_all_state(self) -> dict[str, Any]:
        """Get all current state from the sandbox."""
        result = self.sandbox.execute('''
import json
state = {k: v for k, v in globals().items()
         if not k.startswith('_') and k[0].islower()}
Final = state
''')
        return result if isinstance(result, dict) else {}

    def save_code_artifact(self, name: str, code: str, language: str = "python") -> str:
        """Save code as an artifact."""
        artifact_code = f'''
import os
import json

ARTIFACTS_DIR = "/data/workspace/{self.sandbox.session_id}/artifacts"
artifact_path = os.path.join(ARTIFACTS_DIR, "{name}")

with open(artifact_path, "w") as f:
    f.write({repr(code)})

# Update artifact registry
registry_path = os.path.join(ARTIFACTS_DIR, "_registry.json")
registry = {{}}
if os.path.exists(registry_path):
    with open(registry_path, "r") as f:
        registry = json.load(f)

registry["{name}"] = {{
    "type": "code",
    "language": "{language}",
    "path": artifact_path,
    "timestamp": __import__('time').time()
}}

with open(registry_path, "w") as f:
    json.dump(registry, f, indent=2)

Final = {{"artifact_id": "{name}", "path": artifact_path}}
'''
        result = self.sandbox.execute(artifact_code)
        return result.path if hasattr(result, 'path') else name

    def load_code_artifact(self, name: str) -> str:
        """Load a code artifact."""
        result = self.sandbox.execute(f'''
import os
artifact_path = "/data/workspace/{self.sandbox.session_id}/artifacts/{name}"
with open(artifact_path, "r") as f:
    content = f.read()
Final = content
''')
        return result if isinstance(result, str) else str(result)
```

### 3. RLM-Based Agent Signature

```python
class StatefulAgentSignature(dspy.Signature):
    """Signature for stateful agents using dspy.RLM.

    The agent receives:
    - Current task/request
    - Available state from previous iterations
    - Workspace artifacts that can be loaded

    The agent produces:
    - Code to execute in the sandbox
    - State updates to persist
    - Artifacts to save
    """

    task: str = dspy.InputField(desc="The current task to accomplish")
    available_state: dict = dspy.InputField(desc="Current state available in the sandbox")
    available_artifacts: list[str] = dspy.InputField(desc="List of available artifacts in workspace")

    reasoning: str = dspy.OutputField(desc="Step-by-step reasoning about the approach")
    code_to_execute: str = dspy.OutputField(desc="Python code to execute in the sandbox")
    state_updates: dict = dspy.OutputField(desc="Key-value pairs to update in state")
    artifacts_to_save: list[dict] = dspy.OutputField(desc="Artifacts to save: [{name, content, type}]")
    is_complete: bool = dspy.OutputField(desc="Whether the task is complete")


class StatefulRLMModule(dspy.Module):
    """dspy.RLM-based module for stateful agent execution.

    Integrates dspy.RLM with StatefulSandboxManager for persistent execution.
    """

    def __init__(
        self,
        session_id: str,
        max_iterations: int = 30,
        max_llm_calls: int = 50,
        **sandbox_kwargs
    ):
        super().__init__()

        self.sandbox_manager = StatefulSandboxManager(
            session_id=session_id,
            **sandbox_kwargs
        )
        self.state_manager = AgentStateManager(self.sandbox_manager)

        # Configure RLM with our interpreter
        self.rlm = dspy.RLM(
            signature=StatefulAgentSignature,
            interpreter=self.sandbox_manager._interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
        )

    def forward(self, task: str) -> dspy.Prediction:
        """Execute a task using RLM with stateful sandbox."""

        # Start sandbox and restore state
        self.sandbox_manager.start()

        try:
            # Get current state for context
            available_state = self.state_manager.get_all_state()

            # Run RLM with stateful context
            result = self.rlm(
                task=task,
                available_state=available_state,
                available_artifacts=[],  # TODO: list artifacts
            )

            # Apply state updates
            if result.state_updates:
                self.state_manager.update_state(result.state_updates)

            # Save artifacts
            for artifact in result.artifacts_to_save:
                self.state_manager.save_code_artifact(
                    artifact["name"],
                    artifact["content"],
                    artifact.get("type", "python")
                )

            return result

        finally:
            self.sandbox_manager.shutdown()
```

### 4. Workspace Helpers in driver.py

Add these helpers to the sandbox driver for workspace operations:

```python
# In driver.py, add to sandbox_globals:

def _workspace_path(session_id: str, *paths: str) -> str:
    """Get path within workspace for a session."""
    import os as _os
    base = _os.path.join("/data/workspace", session_id)
    return _os.path.join(base, *paths) if paths else base

def save_workspace_file(session_id: str, filename: str, content: str) -> str:
    """Save a file to the workspace."""
    import os as _os
    path = _workspace_path(session_id, "artifacts", filename)
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path

def load_workspace_file(session_id: str, filename: str) -> str:
    """Load a file from the workspace."""
    import os as _os
    path = _workspace_path(session_id, "artifacts", filename)
    with open(path, "r") as f:
        return f.read()

def list_workspace_files(session_id: str) -> list[dict]:
    """List all files in the workspace."""
    import os as _os
    artifacts_dir = _workspace_path(session_id, "artifacts")
    if not _os.path.exists(artifacts_dir):
        return []

    files = []
    for name in _os.listdir(artifacts_dir):
        path = _os.path.join(artifacts_dir, name)
        stat = _os.stat(path)
        files.append({
            "name": name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "is_file": _os.path.isfile(path)
        })
    return files

def checkpoint_workspace(session_id: str, checkpoint_name: str | None = None) -> str:
    """Create a checkpoint of current workspace state."""
    import os as _os
    import json
    import pickle

    if checkpoint_name is None:
        import time
        checkpoint_name = f"checkpoint_{int(time.time())}"

    checkpoint_dir = _workspace_path(session_id, "checkpoints", checkpoint_name)
    _os.makedirs(checkpoint_dir, exist_ok=True)

    # Save globals (excluding builtins)
    state = {k: v for k, v in globals().items()
             if not k.startswith('_') and k not in ('os', 'json', 'pickle', 'time')}

    with open(_os.path.join(checkpoint_dir, "state.pkl"), "wb") as f:
        pickle.dump(state, f)

    # Copy artifacts
    import shutil
    artifacts_dir = _workspace_path(session_id, "artifacts")
    checkpoint_artifacts = _os.path.join(checkpoint_dir, "artifacts")
    if _os.path.exists(artifacts_dir):
        shutil.copytree(artifacts_dir, checkpoint_artifacts, dirs_exist_ok=True)

    return checkpoint_name

def restore_checkpoint(session_id: str, checkpoint_name: str) -> dict:
    """Restore workspace from a checkpoint."""
    import os as _os
    import pickle
    import shutil

    checkpoint_dir = _workspace_path(session_id, "checkpoints", checkpoint_name)

    # Restore state
    state_path = _os.path.join(checkpoint_dir, "state.pkl")
    if _os.path.exists(state_path):
        with open(state_path, "rb") as f:
            state = pickle.load(f)
        globals().update(state)

    # Restore artifacts
    checkpoint_artifacts = _os.path.join(checkpoint_dir, "artifacts")
    artifacts_dir = _workspace_path(session_id, "artifacts")
    if _os.path.exists(checkpoint_artifacts):
        shutil.copytree(checkpoint_artifacts, artifacts_dir, dirs_exist_ok=True)

    return {"restored": True, "checkpoint": checkpoint_name}

# Register in sandbox_globals
sandbox_globals["_workspace_path"] = _workspace_path
sandbox_globals["save_workspace_file"] = save_workspace_file
sandbox_globals["load_workspace_file"] = load_workspace_file
sandbox_globals["list_workspace_files"] = list_workspace_files
sandbox_globals["checkpoint_workspace"] = checkpoint_workspace
sandbox_globals["restore_checkpoint"] = restore_checkpoint
```

## Usage Example

```python
# Create a stateful agent
agent = StatefulRLMModule(
    session_id="my-task-001",
    volume_name="rlm-workspace",
    max_iterations=30,
    max_llm_calls=50
)

# Execute a task with persistent state
result = agent.forward("""
Analyze the customer data and create a classification model.
Save the model code and evaluation metrics as artifacts.
""")

# Later, resume the same session
agent2 = StatefulRLMModule(session_id="my-task-001")
result2 = agent2.forward("""
Improve the model from the previous session.
Load the saved model and add feature engineering.
""")
```

## Integration with Existing Code

The design integrates with existing fleet-rlm components:

1. **ModalInterpreter**: Used as the CodeInterpreter backend for dspy.RLM
2. **driver.py**: Extended with workspace helpers for state management
3. **react_agent.py**: Can be refactored to use StatefulRLMModule
4. **Volume persistence**: Uses existing Modal Volume infrastructure

## Benefits

1. **True Statefulness**: State persists across RLM iterations and sessions
2. **Agent Continuity**: Agents can resume and improve previous work
3. **Artifact Management**: Code, data, and results are versioned and retrievable
4. **Checkpoint/Restore**: Roll back to previous states if needed
5. **dspy.RLM Native**: Leverages existing RLM orchestration patterns
