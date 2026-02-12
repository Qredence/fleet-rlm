"""Agent state management for persistent, stateful agent workflows.

This module provides the AgentStateManager class that wraps StatefulSandboxManager
with higher-level abstractions for agent-specific operations. It enables agents to:

    - Save and retrieve analysis results across sessions
    - Store and execute generated code scripts
    - Build a persistent knowledge base in Modal Volume
    - Iterate on previous work with full history tracking

The state is persisted to Modal Volume at `/data/agents/{agent_name}/` and survives
across local sessions, enabling truly stateful agent behavior.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

from .sandbox import SandboxResult, StatefulSandboxManager


@dataclass
class AnalysisResult:
    """A persisted analysis result."""

    name: str
    data: dict[str, Any] | str
    timestamp: datetime
    agent_name: str
    version: int = 1
    previous_versions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "agent_name": self.agent_name,
            "version": self.version,
            "previous_versions": self.previous_versions,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AnalysisResult":
        """Create from dictionary."""
        return cls(
            name=d["name"],
            data=d["data"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            agent_name=d["agent_name"],
            version=d.get("version", 1),
            previous_versions=d.get("previous_versions", []),
        )


@dataclass
class CodeScript:
    """A persisted code script with metadata."""

    name: str
    code: str
    timestamp: datetime
    agent_name: str
    description: str = ""
    version: int = 1
    execution_count: int = 0
    last_result: dict[str, Any] | None = None
    previous_versions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "code": self.code,
            "timestamp": self.timestamp.isoformat(),
            "agent_name": self.agent_name,
            "description": self.description,
            "version": self.version,
            "execution_count": self.execution_count,
            "last_result": self.last_result,
            "previous_versions": self.previous_versions,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CodeScript":
        """Create from dictionary."""
        return cls(
            name=d["name"],
            code=d["code"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            agent_name=d["agent_name"],
            description=d.get("description", ""),
            version=d.get("version", 1),
            execution_count=d.get("execution_count", 0),
            last_result=d.get("last_result"),
            previous_versions=d.get("previous_versions", []),
        )


class AgentStateManager:
    """High-level state management for fleet-rlm agents.

    This class provides agent-specific abstractions over StatefulSandboxManager,
    enabling agents to persist their work to Modal Volume and retrieve it
    across sessions. Each agent has its own workspace at
    `/data/agents/{agent_name}/`.

    Example:
        >>> agent = AgentStateManager(agent_name="dspy-analyzer")
        >>> with agent:
        ...     # Save analysis results
        ...     agent.write_analysis("module_inventory", {
        ...         "modules": ["dspy.predict", "dspy.chain"],
        ...         "count": 2
        ...     })
        ...
        ...     # Save a script for later execution
        ...     agent.write_script("extract_modules", '''
        ...         def extract(text):
        ...             return [line for line in text.split("\\n") if "class " in line]
        ...     ''')
        ...
        ...     # Run the saved script
        ...     result = agent.run_script("extract_modules")
        ...
        ...     # Get all previous analyses
        ...     analyses = agent.get_previous_analyses()
        ...
        ...     # Improve an existing script
        ...     new_code = agent.improve_script(
        ...         "extract_modules",
        ...         "Add error handling and docstrings"
        ...     )
    """

    def __init__(
        self,
        agent_name: str,
        session_id: str | None = None,
        volume_name: str | None = "rlm-workspace",
        secret_name: str = "LITELLM",
        timeout: int = 600,
        max_llm_calls: int = 50,
    ) -> None:
        """Initialize the AgentStateManager.

        Args:
            agent_name: Unique name for this agent (used for workspace path).
            session_id: Optional session ID for tracking. Auto-generated if not provided.
            volume_name: Modal Volume name for persistent storage.
            secret_name: Modal Secret name for API keys.
            timeout: Sandbox lifetime timeout in seconds.
            max_llm_calls: Maximum LLM calls per session.
        """
        self.agent_name = agent_name
        self.session_id = session_id or self._generate_session_id()
        self.workspace_root = f"/data/agents/{agent_name}"
        self.analyses_path = f"{self.workspace_root}/analyses"
        self.scripts_path = f"{self.workspace_root}/scripts"
        self.metadata_path = f"{self.workspace_root}/metadata"

        # Initialize the underlying sandbox manager
        self.sandbox = StatefulSandboxManager(
            volume_name=volume_name,
            workspace_path=self.workspace_root,
            secret_name=secret_name,
            timeout=timeout,
            max_llm_calls=max_llm_calls,
        )

        self._initialized = False

    def _generate_session_id(self) -> str:
        """Generate a unique session ID."""
        return f"{self.agent_name}-{uuid.uuid4().hex[:8]}"

    def __enter__(self) -> "AgentStateManager":
        """Start the agent session."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Clean up on exit."""
        self.shutdown()
        return False

    def start(self) -> None:
        """Initialize the agent workspace in the sandbox."""
        if self._initialized:
            return

        self.sandbox.start()

        # Create directory structure
        code = f"""
import os

# Create agent workspace structure
paths = [
    "{self.workspace_root}",
    "{self.analyses_path}",
    "{self.scripts_path}",
    "{self.metadata_path}",
]

for path in paths:
    os.makedirs(path, exist_ok=True)
    print(f"Created: {{path}}")

SUBMIT(status="ok", workspace="{self.workspace_root}")
"""
        self.sandbox.interpreter.execute(code)
        self._initialized = True

    def shutdown(self) -> None:
        """Shutdown the agent session."""
        self.sandbox.shutdown()
        self._initialized = False

    def write_analysis(self, name: str, data: dict[str, Any] | str) -> str:
        """Save analysis result to the persistent workspace.

        Args:
            name: Name identifier for this analysis.
            data: The analysis data (dict or string).

        Returns:
            Path to the saved file.
        """
        self.start()

        # Check if analysis already exists to handle versioning
        existing = self.read_analysis(name)
        version = 1
        previous_versions = []

        if existing:
            version = existing.version + 1
            previous_versions = existing.previous_versions + [existing.to_dict()]

        analysis = AnalysisResult(
            name=name,
            data=data,
            timestamp=datetime.now(timezone.utc),
            agent_name=self.agent_name,
            version=version,
            previous_versions=previous_versions[-5:],  # Keep last 5 versions
        )

        content = json.dumps(analysis.to_dict(), indent=2, default=str)

        result = self.sandbox.save_to_workspace(f"analyses/{name}.json", content)

        if result["status"] == "ok":
            return result["path"]
        else:
            raise RuntimeError(f"Failed to save analysis: {result.get('error')}")

    def read_analysis(self, name: str) -> AnalysisResult | None:
        """Load a previous analysis by name.

        Args:
            name: Name identifier for the analysis.

        Returns:
            AnalysisResult if found, None otherwise.
        """
        self.start()

        result = self.sandbox.load_from_workspace(f"analyses/{name}.json")

        if result["status"] == "ok":
            data = json.loads(result["content"])
            return AnalysisResult.from_dict(data)
        return None

    def get_previous_analyses(self) -> list[AnalysisResult]:
        """List all previous analyses by this agent.

        Returns:
            List of AnalysisResult objects.
        """
        self.start()

        # List all analysis files
        code = f"""
import os
import json

analyses = []
analysis_dir = "{self.analyses_path}"

if os.path.isdir(analysis_dir):
    for filename in os.listdir(analysis_dir):
        if filename.endswith('.json'):
            filepath = os.path.join(analysis_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    analyses.append(data)
            except Exception as e:
                print(f"Error reading {{filename}}: {{e}}")

SUBMIT(analyses=analyses, count=len(analyses))
"""
        try:
            result = self.sandbox.interpreter.execute(code)
            if hasattr(result, "output"):
                output = result.output
            else:
                output = result

            if isinstance(output, dict):
                output_dict = cast(dict[str, Any], output)
                analyses = output_dict.get("analyses")
                if isinstance(analyses, list):
                    return [
                        AnalysisResult.from_dict(a)
                        for a in analyses
                        if isinstance(a, dict)
                    ]
                return []
        except Exception:
            pass

        return []

    def write_script(self, name: str, code: str, description: str = "") -> str:
        """Save a Python script to the workspace.

        Args:
            name: Name identifier for the script (without .py extension).
            code: The Python code to save.
            description: Optional description of what the script does.

        Returns:
            Path to the saved file.
        """
        self.start()

        # Check if script already exists for versioning
        existing = self._load_script_meta(name)
        version = 1
        previous_versions = []

        if existing:
            version = existing["version"] + 1
            previous_versions = existing.get("previous_versions", []) + [
                {
                    "code": existing["code"],
                    "timestamp": existing["timestamp"],
                    "version": existing["version"],
                }
            ]
            # Keep only last 5 versions
            previous_versions = previous_versions[-5:]

        script = CodeScript(
            name=name,
            code=code,
            timestamp=datetime.now(timezone.utc),
            agent_name=self.agent_name,
            description=description,
            version=version,
            previous_versions=previous_versions,
        )

        # Save the script metadata
        result = self.sandbox.save_to_workspace(
            f"scripts/{name}.json", json.dumps(script.to_dict(), indent=2, default=str)
        )

        # Also save the raw code for easy execution
        self.sandbox.save_to_workspace(f"scripts/{name}.py", code)

        if result["status"] == "ok":
            return result["path"]
        else:
            raise RuntimeError(f"Failed to save script: {result.get('error')}")

    def _load_script_meta(self, name: str) -> dict[str, Any] | None:
        """Load script metadata."""
        result = self.sandbox.load_from_workspace(f"scripts/{name}.json")
        if result["status"] == "ok":
            return json.loads(result["content"])
        return None

    @staticmethod
    def _extract_execute_error(result: Any) -> str | None:
        """Return an error message for interpreter results that contain stderr text."""
        if isinstance(result, str):
            stripped = result.strip()
            if "[Error]" in stripped:
                return stripped
        return None

    def read_script(self, name: str) -> CodeScript | None:
        """Load a saved script by name.

        Args:
            name: Name identifier for the script.

        Returns:
            CodeScript if found, None otherwise.
        """
        self.start()

        meta = self._load_script_meta(name)
        if meta:
            return CodeScript.from_dict(meta)
        return None

    def run_script(
        self, name: str, variables: dict[str, Any] | None = None
    ) -> SandboxResult:
        """Execute a previously saved script.

        Args:
            name: Name of the script to run.
            variables: Optional variables to inject into the script.

        Returns:
            SandboxResult with execution results.
        """
        self.start()

        script = self.read_script(name)
        if not script:
            return SandboxResult(
                success=False, output=None, error=f"Script '{name}' not found"
            )

        # Execute the script and classify interpreter-level failures.
        error_message: str | None = None
        result: Any = None
        try:
            result = self.sandbox.interpreter.execute(script.code, variables=variables)
            error_message = self._extract_execute_error(result)
        except Exception as exc:
            error_message = str(exc)

        # Update execution count and last result
        script.execution_count += 1
        script.last_result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": error_message is None,
        }
        if error_message is not None:
            script.last_result["error"] = error_message

        # Save updated metadata
        self.sandbox.save_to_workspace(
            f"scripts/{name}.json", json.dumps(script.to_dict(), indent=2, default=str)
        )

        if error_message is not None:
            return SandboxResult(success=False, output=None, error=error_message)

        return SandboxResult(
            success=True, output=result.output if hasattr(result, "output") else result
        )

    def list_scripts(self) -> list[dict[str, Any]]:
        """List all saved scripts.

        Returns:
            List of script metadata dictionaries.
        """
        self.start()

        code = f"""
import os
import json

scripts = []
scripts_dir = "{self.scripts_path}"

if os.path.isdir(scripts_dir):
    for filename in os.listdir(scripts_dir):
        if filename.endswith('.json'):
            filepath = os.path.join(scripts_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    scripts.append({{
                        "name": data.get("name"),
                        "description": data.get("description", ""),
                        "version": data.get("version", 1),
                        "timestamp": data.get("timestamp"),
                        "execution_count": data.get("execution_count", 0),
                    }})
            except Exception as e:
                print(f"Error reading {{filename}}: {{e}}")

SUBMIT(scripts=scripts, count=len(scripts))
"""
        try:
            result = self.sandbox.interpreter.execute(code)
            if hasattr(result, "output"):
                output = result.output
            else:
                output = result

            if isinstance(output, dict):
                output_dict = cast(dict[str, Any], output)
                scripts = output_dict.get("scripts")
                if isinstance(scripts, list):
                    return [s for s in scripts if isinstance(s, dict)]
                return []
        except Exception:
            pass

        return []

    def improve_script(
        self, name: str, instructions: str, context: dict[str, Any] | None = None
    ) -> str:
        """Load a script, apply improvements, and save a new version.

        This method uses dspy.RLM to generate improved code based on the
        existing script and improvement instructions.

        Args:
            name: Name of the script to improve.
            instructions: Instructions for how to improve the script.
            context: Optional additional context for the improvement.

        Returns:
            The improved code.
        """
        self.start()

        script = self.read_script(name)
        if not script:
            raise ValueError(f"Script '{name}' not found")

        # Build improvement task
        task = f"""Improve the following Python script based on these instructions: {instructions}

Current script (version {script.version}):
```python
{script.code}
```

Please provide the complete improved code."""

        if context:
            task += f"\n\nAdditional context: {context}"

        # Use RLM to generate improved code
        result = self.sandbox.execute_with_rlm(task)

        if result.success:
            improved_code = result.output
            if isinstance(improved_code, dict):
                improved_code = improved_code.get(
                    "generated_code", improved_code.get("output", "")
                )

            # Save as new version
            self.write_script(
                name=name,
                code=improved_code,
                description=f"{script.description} (improved: {instructions[:50]}...)",
            )

            return improved_code
        else:
            raise RuntimeError(f"Failed to improve script: {result.error}")

    def get_session_info(self) -> dict[str, Any]:
        """Get information about the current agent session.

        Returns:
            Dict with session metadata.
        """
        return {
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "sandbox_stats": self.sandbox.get_session_stats(),
        }

    def export_state(self, export_path: str | None = None) -> str:
        """Export the entire agent state to a JSON file.

        Args:
            export_path: Optional path for the export file.

        Returns:
            Path to the exported file.
        """
        self.start()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_name = export_path or f"{self.agent_name}_export_{timestamp}.json"

        analyses = self.get_previous_analyses()
        scripts = self.list_scripts()

        export_data = {
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "analyses": [a.to_dict() for a in analyses],
            "scripts": scripts,
            "sandbox_stats": self.sandbox.get_session_stats(),
        }

        result = self.sandbox.save_to_workspace(
            f"metadata/{export_name}", json.dumps(export_data, indent=2, default=str)
        )

        if result["status"] == "ok":
            return result["path"]
        else:
            raise RuntimeError(f"Failed to export state: {result.get('error')}")
