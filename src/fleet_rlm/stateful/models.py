"""Data models for stateful agent workflows.

This module contains the persisted data models used by AgentStateManager
for storing analysis results and code scripts across sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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
