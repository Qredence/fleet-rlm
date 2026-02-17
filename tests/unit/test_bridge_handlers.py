from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.bridge import (
    handlers_commands,
    handlers_mentions,
    handlers_settings,
    handlers_status,
)
from fleet_rlm.bridge.protocol import BridgeRPCError
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.react.commands import COMMAND_DISPATCH


def test_mentions_search_ranks_matches(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')")

    result = handlers_mentions.search_mentions({"query": "rea", "root": str(tmp_path)})

    assert result["count"] >= 1
    assert any(item["path"].lower().startswith("readme") for item in result["items"])


def test_settings_update_and_get_roundtrip(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    monkeypatch.chdir(project_root)

    update = handlers_settings.update_settings(
        {"updates": {"DSPY_LM_MODEL": "openai/gpt-4o-mini"}}
    )
    snapshot = handlers_settings.get_settings({"keys": ["DSPY_LM_MODEL"]})

    assert "DSPY_LM_MODEL" in update["updated"]
    assert snapshot["values"]["DSPY_LM_MODEL"] == "openai/gpt-4o-mini"
    assert (project_root / ".env").exists()


@pytest.mark.asyncio
async def test_commands_execute_wrapper_and_dispatch(monkeypatch):
    runtime = SimpleNamespace(
        command_permissions={},
        secret_name="LITELLM",
        ensure_agent=lambda: None,
        agent=None,
        config=SimpleNamespace(
            rlm_settings=SimpleNamespace(
                max_iterations=5,
                max_llm_calls=100,
                verbose=False,
            ),
            interpreter=SimpleNamespace(timeout=120),
        ),
        volume_name="demo-volume",
    )
    monkeypatch.setattr(
        handlers_commands.runners,
        "check_secret_presence",
        lambda secret_name: {"secret_name": secret_name, "present": True},
    )
    wrapper_result = await handlers_commands.execute_command(
        runtime, {"command": "check-secret", "args": {}}
    )
    assert wrapper_result["result"]["present"] is True

    dispatched_command = next(iter(COMMAND_DISPATCH))

    class FakeAgent:
        async def execute_command(self, command: str, args: dict):
            return {"command": command, "args": args, "ok": True}

    runtime.agent = FakeAgent()
    dispatch_result = await handlers_commands.execute_command(
        runtime, {"command": dispatched_command, "args": {"x": 1}}
    )
    assert dispatch_result["result"]["ok"] is True
    assert dispatch_result["result"]["command"] == dispatched_command


@pytest.mark.asyncio
async def test_commands_execute_respects_deny_policy():
    runtime = SimpleNamespace(command_permissions={"dangerous": "deny"})
    with pytest.raises(BridgeRPCError) as exc:
        await handlers_commands.execute_command(
            runtime, {"command": "dangerous", "args": {}}
        )
    assert exc.value.code == "DENIED"


def test_status_payload_includes_runtime_and_documents(monkeypatch):
    runtime = SimpleNamespace(
        session_id="abc123",
        trace_mode="compact",
        secret_name="LITELLM",
        volume_name="rlm-volume-dspy",
        command_permissions={"run-long-context": "allow"},
        agent=SimpleNamespace(
            list_documents=lambda: {
                "documents": [{"alias": "doc"}],
                "active_alias": "doc",
            }
        ),
    )
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-demo")
    monkeypatch.setenv("MODAL_TOKEN_ID", "token-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "token-secret")
    monkeypatch.setattr(
        handlers_status,
        "load_modal_config",
        lambda: {"token_id": "profile-id", "token_secret": "profile-secret"},
    )
    monkeypatch.setattr(
        handlers_status,
        "get_default_volume_name",
        lambda: "rlm-data-default",
    )
    monkeypatch.setattr(
        handlers_status.runners,
        "check_secret_presence",
        lambda secret_name: {"secret_name": secret_name, "present": True},
    )

    payload = handlers_status.get_status(runtime)

    assert payload["planner_ready"] is True
    assert payload["documents"]["loaded_count"] == 1
    assert payload["modal"]["workspace_default_volume"] == "rlm-data-default"
    assert payload["secret_check"]["present"] is True


def test_status_payload_when_credentials_missing(monkeypatch):
    runtime = SimpleNamespace(
        session_id="test",
        trace_mode="compact",
        secret_name="LITELLM",
        volume_name="rlm-volume-dspy",
        command_permissions={},
        agent=None,
    )
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.setattr(
        handlers_status,
        "load_modal_config",
        lambda: {},
    )
    monkeypatch.setattr(
        handlers_status,
        "get_default_volume_name",
        lambda: "rlm-data-default",
    )
    monkeypatch.setattr(
        handlers_status.runners,
        "check_secret_presence",
        lambda secret_name: {"secret_name": secret_name, "present": False},
    )

    payload = handlers_status.get_status(runtime)

    assert payload["planner_ready"] is False
    assert payload["secret_check"]["present"] is False


def test_settings_get_masks_secrets(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    monkeypatch.chdir(project_root)
    # Set env vars directly (get_settings reads from os.environ)
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret-key")
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4")

    snapshot = handlers_settings.get_settings(
        {"keys": ["DSPY_LLM_API_KEY", "DSPY_LM_MODEL"]}
    )

    # Mask format is "prefix...suffix" for long keys
    assert snapshot["masked_values"]["DSPY_LLM_API_KEY"].startswith("sk-")
    assert "..." in snapshot["masked_values"]["DSPY_LLM_API_KEY"]
    # Secret values are masked in `values` by default.
    assert (
        snapshot["values"]["DSPY_LLM_API_KEY"]
        == snapshot["masked_values"]["DSPY_LLM_API_KEY"]
    )
    assert snapshot["values"]["DSPY_LM_MODEL"] == "openai/gpt-4"


def test_settings_get_always_masks_secret_values(tmp_path, monkeypatch):
    """Secret values must never appear in cleartext, even when opted-in."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("DSPY_LLM_API_KEY", "sk-super-secret-key")

    snapshot = handlers_settings.get_settings(
        {
            "keys": ["DSPY_LLM_API_KEY"],
            "include_secret_values": True,
        }
    )

    assert "secret_values_included" not in snapshot
    assert snapshot["values"]["DSPY_LLM_API_KEY"] != "sk-super-secret-key"
    assert (
        snapshot["values"]["DSPY_LLM_API_KEY"]
        == snapshot["masked_values"]["DSPY_LLM_API_KEY"]
    )


@pytest.mark.asyncio
async def test_commands_execute_uses_delegate_profile_when_available(monkeypatch):
    entered_profiles: list[ExecutionProfile] = []

    class FakeExecutionProfileCtx:
        def __init__(self, profile: ExecutionProfile) -> None:
            self._profile = profile

        def __enter__(self) -> None:
            entered_profiles.append(self._profile)

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakeInterpreter:
        def execution_profile(
            self, profile: ExecutionProfile
        ) -> FakeExecutionProfileCtx:
            return FakeExecutionProfileCtx(profile)

    dispatched_command = next(iter(COMMAND_DISPATCH))

    class FakeAgent:
        interpreter = FakeInterpreter()

        async def execute_command(self, command: str, args: dict):
            return {"command": command, "args": args, "ok": True}

    runtime = SimpleNamespace(
        command_permissions={},
        secret_name="LITELLM",
        ensure_agent=lambda: None,
        agent=FakeAgent(),
        config=SimpleNamespace(
            rlm_settings=SimpleNamespace(
                max_iterations=5,
                max_llm_calls=100,
                verbose=False,
            ),
            interpreter=SimpleNamespace(timeout=120),
        ),
        volume_name="demo-volume",
    )

    result = await handlers_commands.execute_command(
        runtime,
        {"command": dispatched_command, "args": {"x": 1}},
    )

    assert result["result"]["ok"] is True
    assert entered_profiles == [ExecutionProfile.RLM_DELEGATE]


def test_mentions_search_handles_large_repo(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    src = tmp_path / "src"
    src.mkdir()
    for i in range(100):
        (src / f"file_{i}.py").write_text(f"# file {i}")

    result = handlers_mentions.search_mentions(
        {"query": "file_5", "root": str(tmp_path)}
    )

    assert result["count"] >= 1
    assert result["count"] <= 20  # Should be bounded
    assert any("file_5" in item["path"] for item in result["items"])


def test_mentions_search_reuses_cache_for_repeated_queries(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("print('alpha')")
    (tmp_path / "src" / "beta.py").write_text("print('beta')")

    handlers_mentions._INDEX_CACHE.clear()
    calls = {"count": 0}
    original = handlers_mentions._build_index

    def counting_build_index(root):
        calls["count"] += 1
        return original(root)

    monkeypatch.setattr(handlers_mentions, "_build_index", counting_build_index)

    first = handlers_mentions.search_mentions({"query": "alp", "root": str(tmp_path)})
    second = handlers_mentions.search_mentions({"query": "alp", "root": str(tmp_path)})

    assert first["count"] >= 1
    assert second["count"] >= 1
    assert calls["count"] == 1


def test_mentions_search_ignores_common_large_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "visible.py").write_text("print('ok')")

    ignored = [".git", "node_modules", ".venv", "__pycache__"]
    for name in ignored:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "secret_match.py").write_text("print('hidden')")

    handlers_mentions._INDEX_CACHE.clear()
    result = handlers_mentions.search_mentions(
        {"query": "secret_match", "root": str(tmp_path)}
    )

    assert result["count"] == 0


def test_mentions_search_empty_query_returns_only_top_level(tmp_path):
    (tmp_path / "top.py").write_text("print('top')")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.py").write_text("print('deep')")

    result = handlers_mentions.search_mentions({"query": "", "root": str(tmp_path)})

    paths = [item["path"] for item in result["items"]]
    assert "top.py" in paths
    assert "nested/" in paths
    assert all("/" not in path.rstrip("/") for path in paths)


def test_mentions_search_path_prefix_scopes_to_subtree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "server.py").write_text("print('src')")
    (tmp_path / "docs" / "server.py").write_text("print('docs')")

    result = handlers_mentions.search_mentions(
        {"query": "src/serv", "root": str(tmp_path)}
    )

    assert result["count"] >= 1
    assert all(item["path"].startswith("src/") for item in result["items"])
    assert all(not item["path"].startswith("docs/") for item in result["items"])


def test_commands_list_returns_all_commands():
    result = handlers_commands.list_commands()

    assert result["count"] > 0
    assert "check-secret" in result["wrapper_commands"]
    assert len(result["tool_commands"]) > 0


def test_settings_update_creates_env_file(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    monkeypatch.chdir(project_root)

    assert not (project_root / ".env").exists()

    handlers_settings.update_settings(
        {"updates": {"DSPY_LM_MODEL": "anthropic/claude-3"}}
    )

    assert (project_root / ".env").exists()
    content = (project_root / ".env").read_text()
    assert "DSPY_LM_MODEL" in content
    assert "anthropic/claude-3" in content
