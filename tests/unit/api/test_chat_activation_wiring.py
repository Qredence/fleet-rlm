"""Unit tests for chat-runtime workspace Skill activation preload."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.api.runtime_services.chat_runtime import attach_workspace_skill_activations
from fleet_rlm.quality.activation_resolve import (
    apply_activated_skill_markdown,
    load_workspace_skill_activation_map,
)


class _Agent:
    def __init__(self) -> None:
        self.activated_skill_markdown: dict[str, str] = {}
        self.agent = SimpleNamespace(activated_skill_markdown={})


@pytest.mark.asyncio
async def test_load_workspace_skill_activation_map_from_list(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("# Activated skill body\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_row = SimpleNamespace(
        target_kind="skill",
        target_id="long-context",
        artifact_path=str(path),
        artifact_sha256=digest,
    )
    activation_row = SimpleNamespace(target_id="long-context")

    class _Persistence:
        async def list_target_activations(self, **_kwargs: object) -> list[tuple[object, object]]:
            return [(activation_row, artifact_row)]

    mapping = await load_workspace_skill_activation_map(
        _Persistence(),
        tenant_id="t",
        workspace_id="w",
    )
    assert mapping == {"long-context": "# Activated skill body\n"}


@pytest.mark.asyncio
async def test_load_workspace_skill_activation_map_fail_closed_on_error() -> None:
    class _Persistence:
        async def list_target_activations(self, **_kwargs: object) -> list[tuple[object, object]]:
            raise RuntimeError("db down")

    mapping = await load_workspace_skill_activation_map(
        _Persistence(),
        tenant_id="t",
        workspace_id="w",
    )
    assert mapping == {}


@pytest.mark.asyncio
async def test_attach_workspace_skill_activations_sets_agent_and_module(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("override-md", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_row = SimpleNamespace(
        target_kind="skill",
        target_id="demo-skill",
        artifact_path=str(path),
        artifact_sha256=digest,
    )

    class _Persistence:
        async def list_target_activations(self, **_kwargs: object) -> list[tuple[object, object]]:
            return [(SimpleNamespace(), artifact_row)]

    agent = _Agent()
    identity = SimpleNamespace(tenant_id="t", workspace_id="w", user_id="u")
    mapping = await attach_workspace_skill_activations(
        agent,
        persistence=_Persistence(),
        identity_rows=identity,
    )
    assert mapping == {"demo-skill": "override-md"}
    assert agent.activated_skill_markdown == {"demo-skill": "override-md"}
    assert agent.agent.activated_skill_markdown == {"demo-skill": "override-md"}


@pytest.mark.asyncio
async def test_attach_skips_without_identity() -> None:
    agent = _Agent()

    class _Persistence:
        async def list_target_activations(self, **_kwargs: object) -> list[tuple[object, object]]:
            raise AssertionError("must not list without identity")

    mapping = await attach_workspace_skill_activations(
        agent,
        persistence=_Persistence(),
        identity_rows=None,
    )
    assert mapping == {}
    assert agent.activated_skill_markdown == {}


def test_apply_activated_skill_markdown_copies_to_inner_module() -> None:
    agent = _Agent()
    apply_activated_skill_markdown(agent, {"x": "y"})
    assert agent.activated_skill_markdown == {"x": "y"}
    assert agent.agent.activated_skill_markdown == {"x": "y"}
