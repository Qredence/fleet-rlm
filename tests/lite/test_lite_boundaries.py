from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.adapters.chat_adapter import ChatAdapter
from dspy.utils.exceptions import AdapterParseError
from fastapi.testclient import TestClient

from fleet_rlm.config import FleetSettings
from fleet_rlm.dspy_support import FleetRLMChatAdapter
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.paths import _workspace_relative_path, session_volume_subpath, validate_session_id
from fleet_rlm.server import create_app
from fleet_rlm.skills import SANDBOX_SKILL_HELPERS_CODE, SkillManager, parse_and_validate_skill_md
from fleet_rlm.store.harness import VolumePostgresHarness
from fleet_rlm.workspace import WorkspaceDataset


@pytest.mark.parametrize("session_id", ["finance-q4", "session_01", "2e8c98b5-5242-4e3b-91b4-2bf1e8c24f0b"])
def test_session_ids_map_to_isolated_volume_subpaths(session_id: str) -> None:
    validate_session_id(session_id)

    assert session_volume_subpath(session_id) == f"sessions/{session_id}"


@pytest.mark.parametrize("session_id", ["../other", "a/b", "/root", "", ".", "..", "bad id"])
def test_session_ids_reject_volume_path_syntax(session_id: str) -> None:
    with pytest.raises(ValueError):
        validate_session_id(session_id)


def test_skill_frontmatter_uses_yaml_and_preserves_colons_in_description() -> None:
    raw = """---
name: ledger-audit
description: "Audits ledgers: use when checking totals."
---

Check the total with Python.
"""

    metadata, body = parse_and_validate_skill_md("ledger-audit", raw)

    assert metadata == {
        "name": "ledger-audit",
        "description": "Audits ledgers: use when checking totals.",
    }
    assert body == "Check the total with Python."


def test_skill_frontmatter_rejects_folder_name_mismatch() -> None:
    raw = "---\nname: another-skill\ndescription: example\n---\n\nInstructions\n"

    with pytest.raises(ValueError, match="must match folder name"):
        parse_and_validate_skill_md("ledger-audit", raw)


def test_workspace_paths_are_rooted_and_have_bounded_names() -> None:
    assert _workspace_relative_path("files/quarter-1.csv") == "files/quarter-1.csv"
    for path in ("/etc/passwd", "files/../../secret", "skills\\escape", "files/a\x00b", "files/" + "a" * 1020):
        with pytest.raises(ValueError):
            _workspace_relative_path(path)


def test_workspace_file_transfer_handles_uploads_larger_than_exec_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"fleet-data" * 250_000

    class FileSystem:
        uploaded: tuple[bytes, str, int] | None = None

        def upload_file(self, data: bytes, path: str, *, timeout: int) -> None:
            self.uploaded = (data, path, timeout)

        def download_file(self, path: str, timeout: int) -> bytes:
            assert path == "/workspace/files/large.csv"
            assert timeout == 1800
            return content

    filesystem = FileSystem()
    sandbox = SimpleNamespace(state=None, fs=filesystem)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._client = object()
    manager._sandbox = sandbox

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str) -> str:
        assert sandbox_arg is sandbox
        assert len(code.encode("utf-8")) < 10_000
        if "if not os.path.isfile(target)" in code:
            return json.dumps({"size": len(content)})
        return json.dumps({"size": None})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.write_workspace_file("files/large.csv", content) == "/workspace/files/large.csv"
    assert filesystem.uploaded == (content, "/workspace/files/large.csv", 1800)
    assert manager.read_workspace_file("files/large.csv", max_bytes=len(content)) == content


@pytest.mark.parametrize("subpath", ["/shared", "shared/../x", "shared//x", "shared/"])
def test_shared_notes_subpath_must_be_a_relative_volume_prefix(subpath: str) -> None:
    with pytest.raises(ValueError):
        FleetSettings(fleet_shared_notes_subpath=subpath)


def test_shared_notes_subpath_defaults_and_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SHARED_NOTES_SUBPATH", raising=False)
    assert FleetSettings().fleet_shared_notes_subpath == "shared/learned-notes"
    assert FleetSettings(fleet_shared_notes_subpath="").fleet_shared_notes_subpath == ""


def test_root_sandbox_mounts_shared_notes_beside_its_session_subtree() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    mounts = root.volume_mounts(child=False)
    assert [(mount.mount_path, mount.subpath) for mount in mounts] == [
        ("/workspace", "sessions/session-a"),
        ("/learned", "shared/learned-notes"),
    ]
    assert {mount.volume_id for mount in mounts} == {"volume"}
    child_mounts = root.volume_mounts(child=True)
    assert [(mount.mount_path, mount.subpath) for mount in child_mounts] == [
        ("/workspace", f"agents/session-a/{root.agent_id}")
    ]


def test_shared_notes_mount_is_optional_and_never_reaches_child_sandboxes() -> None:
    plain = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert [mount.mount_path for mount in plain.volume_mounts(child=False)] == ["/workspace"]
    shared = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    assert [mount.mount_path for mount in shared.volume_mounts(child=True)] == ["/workspace"]


def test_learned_note_lands_as_an_unreviewed_redacted_file_on_the_shared_mount(monkeypatch) -> None:
    uploads: list[tuple[bytes, str]] = []
    commands: list[str] = []
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(upload_file=lambda content, path, **_kwargs: uploads.append((content, path)))
    )
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, code: commands.append(code) or "True\n"
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    path = manager.write_learned_note("turn-0123456789", "Use api_key=hidden\n\nAlways verify digits.", "tr-1")

    assert path == "/learned/session-a-turn0123.md"
    assert uploads[0][1] == path
    assert uploads[0][0].decode("utf-8") == (
        "---\nsession: session-a\nturn: turn0123\ntrace_id: tr-1\nstatus: unreviewed\n"
        "summary: Use api_key=[redacted]\n---\nUse api_key=[redacted]\n\nAlways verify digits.\n"
    )
    assert "/learned" in commands[0]


def test_learned_note_is_skipped_when_the_sandbox_predates_the_mount(monkeypatch) -> None:
    requested: list[bool] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "False\n")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: requested.append(True))

    assert manager.write_learned_note("turn-1", "A note.") is None
    assert requested == []


def test_learned_note_write_is_skipped_for_empty_notes_and_disabled_mounts(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: pytest.fail("no sandbox call expected")
    )
    enabled = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    disabled = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert enabled.write_learned_note("turn-1", "  \n") is None
    assert disabled.write_learned_note("turn-1", "A note.") is None


def test_shared_notes_listing_is_sandbox_safe_bounded_and_degrades_to_empty(monkeypatch) -> None:
    codes: list[str] = []
    rows = json.dumps([{"file": "session-a-turn0123.md", "summary": "Use Chudnovsky"}])
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, code: codes.append(code) or rows)

    assert manager.list_learned_notes() == [{"file": "session-a-turn0123.md", "summary": "Use Chudnovsky"}]
    # Generated sandbox source must not contain JSON literals: they raise NameError there.
    compile(codes[0], "<learned-notes>", "exec")
    for literal in ("true", "false", "null"):
        assert literal not in codes[0]

    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "not json")
    assert manager.list_learned_notes() == []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: '{"file": "x"}')
    assert manager.list_learned_notes() == []

    def unreachable(_self, _code):
        raise RuntimeError("sandbox dropped")

    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", unreachable)
    assert manager.list_learned_notes() == []
    assert manager.list_learned_notes(limit=99) == []
    assert DaytonaSandboxManager("key", "org", "volume", "session-a").list_learned_notes() == []


def test_dspy_history_is_disabled_for_session_turn_contexts() -> None:
    lm = dspy.utils.DummyLM([{"answer": "isolated result"}])
    predictor = dspy.Predict("task -> answer")

    with dspy.context(lm=lm, disable_history=True):
        assert predictor(task="private session task").answer == "isolated result"

    assert lm.history == []


@pytest.mark.parametrize("reasoning", ["", "The header is `id,value`. I'll stream the full file.\n\n"])
@pytest.mark.parametrize("suffix", ["", "\n\n[[ ## completed ## ]]\n"])
def test_fleet_rlm_adapter_accepts_code_only_python_fences(reasoning: str, suffix: str) -> None:
    signature = dspy.Signature("task -> reasoning, code")
    adapter = FleetRLMChatAdapter()

    assert adapter.parse(signature, reasoning + "```python\nprint(17 + 23 + 41)\n```" + suffix) == {
        "reasoning": reasoning.strip(),
        "code": "print(17 + 23 + 41)",
    }


def test_fleet_rlm_adapter_leaves_other_outputs_to_the_standard_parser() -> None:
    signature = dspy.Signature("task -> answer")
    adapter = FleetRLMChatAdapter()

    assert adapter.parse(signature, "[[ ## answer ## ]]\n42\n[[ ## completed ## ]]") == {"answer": "42"}


def test_fleet_rlm_adapter_drops_leaked_thinking_before_the_answer() -> None:
    # Some providers leak the model's thinking into content, closed by </think>, before the real answer.
    leaked_code = "Draft:\n```python\nprint('draft')\n```\nWait, rewrite it.</think>```python\nprint(3.14)\n```"
    leaked_fields = "Hmm, use the typed field.</think>[[ ## answer ## ]]\n42\n[[ ## completed ## ]]"

    assert FleetRLMChatAdapter().parse(dspy.Signature("task -> reasoning, code"), leaked_code) == {
        "reasoning": "",
        "code": "print(3.14)",
    }
    assert FleetRLMChatAdapter().parse(dspy.Signature("task -> answer"), leaked_fields) == {"answer": "42"}


def test_invalid_compiled_program_does_not_replace_the_last_good_session_artifact(tmp_path: Path) -> None:
    compiled_path = tmp_path / "session-a.json"
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
        compiled_path=str(compiled_path),
    )
    engine.save(str(compiled_path))
    known_good = compiled_path.read_bytes()
    corrupt = json.loads(known_good)
    corrupt["flex_agent"]["module_src"] = "class Invalid(:\n"

    with pytest.raises((KeyError, SyntaxError, ValueError)):
        engine.load_compiled_program(json.dumps(corrupt).encode("utf-8"))

    assert compiled_path.read_bytes() == known_good
    assert engine.flex_agent.module_src == json.loads(known_good)["flex_agent"]["module_src"]


def test_compiled_program_path_is_not_shared_by_default() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
    )

    assert engine.compiled_path is None


def test_local_skill_helpers_support_progressive_disclosure_and_reject_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    helpers = {}
    source = SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})")
    exec(source, helpers)

    helpers["save_skill"](
        "ledger-audit",
        "Audit ledger totals when a reconciliation is requested.",
        "Read the source ledger and verify each total with Python.",
        scripts={"audit.py": "print('audit complete')\n"},
        references={"policy.md": "Amounts are stored in EUR.\n"},
    )
    assert helpers["list_skills"]() == [
        {"name": "ledger-audit", "description": "Audit ledger totals when a reconciliation is requested."}
    ]
    active_skill = helpers["use_skill"]("ledger-audit")
    assert "audit.py" in active_skill
    assert helpers["read_skill_reference"]("ledger-audit", "references/policy.md") == "Amounts are stored in EUR.\n"
    assert helpers["run_skill_script"]("ledger-audit", "scripts/audit.py") == "audit complete\n"

    outside = tmp_path / "outside.md"
    outside.write_text("not a Skill reference", encoding="utf-8")
    (root / "ledger-audit" / "references" / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        helpers["read_skill_reference"]("ledger-audit", "references/escape.md")


def test_local_skill_sync_prevalidates_all_skills_before_writing(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    valid = skills_root / "a-valid-skill"
    invalid = skills_root / "z-invalid-skill"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "SKILL.md").write_text("---\nname: a-valid-skill\ndescription: valid\n---\n\nInstructions.\n")
    (invalid / "SKILL.md").write_text("missing frontmatter\n")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []

        def write_workspace_file(self, relative_path: str, content: bytes) -> None:
            self.writes.append((relative_path, content))

    sandbox = RecordingSandbox()
    manager = SkillManager(sandbox, skills_root)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="frontmatter"):
        manager.sync_local_skills_to_volume()
    assert sandbox.writes == []


def test_history_is_bounded_and_keeps_the_newest_turns_in_order() -> None:
    turns = [{"task": f"task-{index}", "answer": f"answer-{index}"} for index in range(10)]

    history = VolumePostgresHarness._render_history(turns)

    assert "task-0" not in history
    assert history.index("task-2") < history.index("task-9")
    assert len(history) <= 16_000

    oversized = VolumePostgresHarness._render_history([{"task": "task-" + "x" * 50_000, "answer": "tail" * 20_000}])
    assert len(oversized) <= 16_000
    assert oversized.endswith("tail")


def test_databricks_mlflow_configuration_requires_governed_trace_storage() -> None:
    with pytest.raises(ValueError, match="Unity Catalog"):
        FleetSettings(
            mlflow_mode="databricks",
            mlflow_tracking_uri="databricks",
            mlflow_trace_catalog="",
            mlflow_trace_schema="",
            mlflow_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_mode="databricks",
        mlflow_tracking_uri="databricks",
        mlflow_experiment_name="/Shared/fleet-rlm",
        mlflow_trace_catalog="fleet",
        mlflow_trace_schema="traces",
        mlflow_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.observability as memory_and_eval

    calls: dict[str, Any] = {}
    monkeypatch.setattr(memory_and_eval.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("uri", uri))
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "set_experiment",
        lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "dspy",
        SimpleNamespace(autolog=lambda **kwargs: calls.setdefault("autolog", kwargs)),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(experiment_id="experiment-id"),
    )

    experiment_id = memory_and_eval.configure_mlflow_observability(
        "fleet-rlm-test",
        "sqlite:///mlflow-test.db",
    )

    assert experiment_id == "experiment-id"
    assert calls == {
        "uri": "sqlite:///mlflow-test.db",
        "experiment": "fleet-rlm-test",
        "autolog": {
            "log_traces": True,
            "log_traces_from_compile": False,
            "log_traces_from_eval": False,
        },
    }


def test_typesafe_route_filters_the_incompatible_mlflow_callback() -> None:
    import fleet_rlm.engine.programs as memory_and_eval

    mlflow_callback = type("MlflowCallback", (), {"__module__": "mlflow.dspy.callback"})()
    sse_callback = object()

    assert memory_and_eval._without_mlflow_dspy_callback([mlflow_callback, sse_callback]) == [sse_callback]


def test_unity_gateway_models_use_native_dspy_engine_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_lm(model: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((model, kwargs))
        return SimpleNamespace(model=model, kwargs=kwargs)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="openai/uscentral.ai_gateway.deepseek-v4-1-flash-service",
        fleet_sub_model="openai/system.ai.claude-haiku-5-5",
        fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
        databricks_host="workspace.example.com",
        databricks_token="test-token",
        fleet_llm_base_url="",
    )

    root_lm, sub_lm, reflection_lm, _ = settings.build_lms()

    gateway_options = {
        "api_base": "https://workspace.example.com/ai-gateway/mlflow/v1",
        "api_key": "test-token",
        "engine": "lm15",
        "cache": False,
        "num_retries": 3,
    }
    assert root_lm.kwargs == gateway_options
    assert sub_lm.kwargs == gateway_options
    assert reflection_lm.kwargs == gateway_options
    assert calls == [
        ("openai/uscentral.ai_gateway.deepseek-v4-1-flash-service", gateway_options),
        ("openai/system.ai.claude-haiku-5-5", gateway_options),
        ("openai/system.ai.claude-sonnet-5-5", gateway_options),
    ]
    assert "test-token" not in repr(settings)


def test_typesafe_route_uses_the_configured_model_not_the_client_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_lm(model: str, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(model=model)

    def fake_typesafe(model: str, *, api_key: str) -> SimpleNamespace:
        calls.append((model, {"api_key": api_key}))
        return SimpleNamespace(model=model)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    monkeypatch.setattr(dspy.experimental, "TypeSafe", fake_typesafe)
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://unused:unused@localhost/fleet",
        fleet_root_model="openai/test-root",
        fleet_sub_model="openai/test-sub",
        fleet_reflection_model="openai/test-reflection",
        fleet_llm_base_url="https://workspace.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
        typesafe_model="jev-1.14.0",
        typesafe_api_key="typesafe-test-token",
    )

    *_, jev_lm = settings.build_lms()

    assert settings.typesafe_model == "jev-1.14.0"
    assert jev_lm.model == "jev-1.14.0"
    assert calls == [("jev-1.14.0", {"api_key": "typesafe-test-token"})]


def test_route_selection_logs_specific_exception_without_exposing_credentials(caplog: pytest.LogCaptureFixture) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
        jev_lm=SimpleNamespace(api_key="typesafe-test-token"),
    )

    class FailingTriage:
        def __call__(self, **_kwargs: Any) -> Any:
            raise RuntimeError("403: invalid token typesafe-test-token")

    engine.triage = FailingTriage()  # type: ignore[assignment]
    engine.primary_rlm = lambda **_kwargs: SimpleNamespace(  # type: ignore[assignment]
        answer="bounded fallback",
        learned_skill_note="",
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.engine.programs"):
        prediction = engine(
            workspace_manifest="",
            memory_summary="",
            task="test fallback",
        )

    assert prediction.route == "rlm"
    assert prediction.answer == "bounded fallback"
    assert "RuntimeError" in caplog.text
    assert "403: invalid token" in caplog.text
    assert "typesafe-test-token" not in caplog.text


def test_non_unity_gateway_models_are_rejected_without_backend_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_lm(model: str, **_kwargs: Any) -> SimpleNamespace:
        calls.append(model)
        return SimpleNamespace(model=model)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        FleetSettings(
            _env_file=None,
            fleet_root_model="databricks/databricks-claude-sonnet-5.5",
            fleet_sub_model="openai/system.ai.claude-haiku-5-5",
            fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
            databricks_host="workspace.example.com",
            databricks_token="test-token",
        )

    assert calls == []


def test_unity_gateway_models_require_token_and_https_base_url() -> None:
    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="openai/system.ai.claude-haiku-5-5",
        databricks_host="",
        databricks_token="",
        fleet_llm_base_url="",
    )
    with pytest.raises(ValueError, match="HTTPS workspace URL"):
        settings.build_lms()

    with pytest.raises(ValueError, match="HTTPS workspace URL"):
        FleetSettings(
            _env_file=None,
            fleet_root_model="openai/system.ai.claude-haiku-5-5",
            fleet_llm_base_url="http://gateway.example.com/ai-gateway/mlflow/v1",
            databricks_token="test-token",
        )


def test_fleet_prefixed_mlflow_environment_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACKING_URI", "sqlite:///fleet-test.db")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)

    assert settings.mlflow_tracking_uri == "sqlite:///fleet-test.db"
    assert settings.mlflow_experiment_name == "fleet-test"


def _unity_gateway_test_settings() -> FleetSettings:
    return FleetSettings(
        _env_file=None,
        fleet_root_model="openai/system.ai.deepseek-v4-1-flash",
        fleet_sub_model="openai/system.ai.claude-haiku-5-5",
        fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
        fleet_llm_base_url="https://workspace.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
    )


def test_fastapi_health_and_lite_route_contract() -> None:
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)
    expected_paths = {
        "/health",
        "/sessions",
        "/skills",
        "/skills/pull",
        "/sessions/{session_id}/files",
        "/sessions/{session_id}/attach",
        "/sessions/{session_id}/turn",
    }
    openapi = app.openapi()
    assert expected_paths <= set(openapi["paths"])
    turn_content = openapi["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]
    assert turn_content["text/event-stream"]["schema"] == {"type": "string"}

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.8.0"}


@pytest.mark.parametrize("write_fails", [False, True])
def test_turn_sse_persists_and_returns_one_mocked_session_turn(
    monkeypatch: pytest.MonkeyPatch, write_fails: bool
) -> None:
    import fleet_rlm.server as server

    files = {"files/input.txt": b"initial input", "files/rates.csv": b"rate\n3\n"}
    records = []
    order: list[str] = []
    written: list[tuple[str, str, Any]] = []

    def run_engine(**_kwargs):
        assert "- older.md: prior hint" in _kwargs["memory_summary"]
        files["files/input.txt"] = b"modified during execution"
        return SimpleNamespace(
            answer="processed locally", route="flex", learned_skill_note="Reuse the digit window check."
        )

    def persist(**kwargs):
        records.append(kwargs["turn_record"])
        order.append("sync")
        return ["artifacts/result.csv"]

    def write_note(turn_id, note, trace_id=None):
        order.append("write")
        if write_fails:
            raise RuntimeError("shared mount is gone")
        written.append((turn_id, note, trace_id))

    class RuntimeRegistry:
        def __init__(self, _settings: FleetSettings, _lms: tuple[Any, Any, Any, Any | None], _store: Any = None):
            self.runtime = SimpleNamespace(
                sandbox_mgr=SimpleNamespace(
                    list_workspace_files=lambda: list(reversed(files)),
                    read_workspace_file=files.__getitem__,
                    list_learned_notes=lambda: [{"file": "older.md", "summary": "prior hint"}],
                    write_learned_note=write_note,
                ),
                harness=SimpleNamespace(
                    hydrate_volume_from_postgres=lambda _session_id: ("summary", "prior history"),
                    sync_volume_to_postgres=persist,
                ),
                skill_mgr=SimpleNamespace(
                    sync_local_skills_to_volume=lambda: 1,
                    build_catalog_xml=lambda: "<available_skills></available_skills>",
                ),
                context=WorkspaceDataset("files/input.txt"),
                engine=run_engine,
                turn_lock=asyncio.Lock(),
            )

        async def get(self, _session_id: str) -> Any:
            return self.runtime

        async def close(self) -> None:
            return None

    @contextmanager
    def fake_span(**_kwargs: Any) -> Any:
        yield SimpleNamespace(
            set_inputs=lambda _value: None, set_outputs=lambda _value: None, set_attributes=lambda _value: None
        )

    global_callback = object()
    observed_callbacks: list[Any] = []

    @contextmanager
    def fake_dspy_context(*, callbacks: list[Any], disable_history: bool, **_extra: Any) -> Any:
        assert disable_history is True
        observed_callbacks.extend(callbacks)
        yield

    import asyncio

    monkeypatch.setattr(server, "SessionRuntimeRegistry", RuntimeRegistry)
    monkeypatch.setattr(
        server,
        "dspy",
        SimpleNamespace(settings=SimpleNamespace(callbacks=[global_callback]), context=fake_dspy_context),
    )
    monkeypatch.setattr(server.mlflow, "start_span", fake_span)
    trace_updates: list[dict] = []
    monkeypatch.setattr(server.mlflow, "update_current_trace", lambda **kwargs: trace_updates.append(kwargs))
    monkeypatch.setattr(server.mlflow, "set_experiment", lambda **_kwargs: None)
    monkeypatch.setattr(server.mlflow, "get_last_active_trace_id", lambda: "trace-test-id")
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)

    with TestClient(app) as client:
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "summarize this file"})

    assert {
        "metadata": {"mlflow.trace.session": "session-a"},
        "request_preview": "summarize this file",
    } in trace_updates
    assert {"response_preview": "processed locally"} in trace_updates

    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert events[0]["event"] == "turn_status"
    assert events[0]["payload"] == {"phase": "preparation"}
    assert events[1]["event"] == "turn_complete"
    assert events[1]["payload"] == {
        "answer": "processed locally",
        "route": "flex",
        "learned_skill_note": "Reuse the digit window check.",
        "artifacts": ["artifacts/result.csv"],
        "trace_id": "trace-test-id",
    }
    assert global_callback in observed_callbacks
    assert any(isinstance(callback, server.SSERuntimeCallback) for callback in observed_callbacks)
    assert records[0]["context_files"] == {
        "files/input.txt": hashlib.sha256(b"initial input").hexdigest(),
        "files/rates.csv": hashlib.sha256(b"rate\n3\n").hexdigest(),
    }
    assert records[0]["workspace_manifest"] == "/workspace/files/input.txt, /workspace/files/rates.csv"
    assert order == ["sync", "write"]
    assert written == ([] if write_fails else [(records[0]["turn_id"], "Reuse the digit window check.", None)])
    # The server uses "done" as its internal queue sentinel; the HTTP stream
    # ends after it and deliberately exposes only user-facing events.
    assert len(events) == 2


def test_cancelled_manager_cannot_restart_remote_execution() -> None:
    stopped = threading.Event()
    sandbox = SimpleNamespace(id="sandbox-cancel", state="started")
    manager = DaytonaSandboxManager("key", "org", "volume", "cancel-test")
    manager._sandbox = sandbox
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: stopped.set())
    manager.cancel_turn()
    assert stopped.is_set()
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.ensure_sandbox()
    manager.reset_turn_budget()
    manager.end_turn()
    assert manager.ensure_sandbox() is sandbox


def test_dspy_activity_stream_correlates_calls_without_predictor_contents() -> None:
    import asyncio

    from fleet_rlm.server import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def run() -> None:
            def lookup(query: str, api_key: str) -> dict[str, str]:  # noqa: ARG001
                return {"result": "found invoice", "access_token": "result-secret", "reasoning": "private-result"}

            with dspy.context(callbacks=[callback], lm=dspy.utils.DummyLM([{"answer": "approved"}])):
                dspy.Tool(lookup)(query="invoice 17", api_key="input-secret")
                dspy.Predict("question -> answer")(question="private-prompt")

        await asyncio.to_thread(run)
        callback.close()
        return [queue.get_nowait() for _ in range(queue.qsize())]

    events = asyncio.run(scenario())
    starts = {event["call_id"]: event for event in events if event["event"].endswith("_start")}
    ends = [event for event in events if event["event"].endswith("_end")]
    assert {event["event"] for event in events} >= {"tool_start", "tool_end", "module_start", "module_end"}
    for end in ends:
        assert end["call_id"] in starts
        assert end["duration_ms"] >= 0
        assert end["ok"] is True
    serialized = json.dumps(events)
    assert "invoice 17" in serialized and "found invoice" in serialized
    assert all(
        secret not in serialized for secret in ("input-secret", "result-secret", "private-result", "private-prompt")
    )
    assert "[redacted]" in serialized


def test_child_parse_failure_is_returned_to_the_parent_instead_of_aborting_the_turn(monkeypatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
    )
    tools = engine.primary_rlm._user_tools

    class FailingChild:
        def __init__(self, *_args, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")

        def __call__(self, **_kwargs):
            raise AdapterParseError(
                adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[ ## reasoning ## ]]"
            )

    monkeypatch.setattr(dspy, "RLM", FailingChild)

    result = tools["spawn_agent"](subtask="count refunds in files/a.log", input_paths=[])
    assert result["findings"] == "" and result["artifacts"] == []
    assert result["error"].startswith("child agent failed (AdapterParseError)")
    assert tools["sub_rlm"](subtask="investigate").startswith("ERROR: child agent failed (AdapterParseError)")


def test_child_budget_exhaustion_still_aborts_the_turn(monkeypatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
    )

    class ExhaustedChild:
        def __init__(self, *_args, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")

        def __call__(self, **_kwargs):
            raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    monkeypatch.setattr(dspy, "RLM", ExhaustedChild)

    with pytest.raises(RuntimeError, match="budget exhausted"):
        engine.primary_rlm._user_tools["spawn_agent"](subtask="subtask", input_paths=[])


def _parse_error() -> AdapterParseError:
    return AdapterParseError(adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[")


def _call_adapter(adapter: FleetRLMChatAdapter):
    return adapter(None, {}, dspy.Signature("a -> b"), [], {"a": "x"})


def test_adapter_reasks_once_then_returns_the_valid_reply(monkeypatch) -> None:
    calls = []

    def flaky(_self, *_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _parse_error()
        return [{"b": "ok"}]

    monkeypatch.setattr(ChatAdapter, "__call__", flaky)
    assert _call_adapter(FleetRLMChatAdapter()) == [{"b": "ok"}]
    assert len(calls) == 2


def test_adapter_gives_up_after_the_retry_budget(monkeypatch) -> None:
    attempts = []

    def always_bad(_self, *_args, **_kwargs):
        attempts.append(1)
        raise _parse_error()

    monkeypatch.setattr(ChatAdapter, "__call__", always_bad)
    with pytest.raises(AdapterParseError):
        _call_adapter(FleetRLMChatAdapter(parse_retries=2))
    assert len(attempts) == 3


def test_adapter_does_not_retry_other_errors(monkeypatch) -> None:
    attempts = []

    def exhausted(_self, *_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    monkeypatch.setattr(ChatAdapter, "__call__", exhausted)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        _call_adapter(FleetRLMChatAdapter())
    assert len(attempts) == 1


def test_adapter_async_path_reasks_too(monkeypatch) -> None:
    import asyncio

    calls = []

    async def flaky(_self, *_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _parse_error()
        return [{"b": "ok"}]

    monkeypatch.setattr(ChatAdapter, "acall", flaky)
    result = asyncio.run(FleetRLMChatAdapter().acall(None, {}, dspy.Signature("a -> b"), [], {"a": "x"}))
    assert result == [{"b": "ok"}] and len(calls) == 2


@pytest.mark.parametrize("fail_preparation", [False, True])
def test_failed_turn_error_event_carries_the_trace_id(monkeypatch: pytest.MonkeyPatch, fail_preparation: bool) -> None:
    import asyncio

    import fleet_rlm.server as server

    def hydrate(_session_id):
        if fail_preparation:
            raise RuntimeError("preparation failed")
        return "summary", "history"

    def failing_engine(**_kwargs):
        raise AdapterParseError(adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[")

    class RuntimeRegistry:
        def __init__(self, _settings: FleetSettings, _lms: tuple[Any, Any, Any, Any | None], _store: Any = None):
            self.runtime = SimpleNamespace(
                sandbox_mgr=SimpleNamespace(
                    list_workspace_files=lambda: [],
                    read_workspace_file=lambda _path: b"",
                    list_learned_notes=lambda: [],
                ),
                harness=SimpleNamespace(
                    hydrate_volume_from_postgres=hydrate,
                    sync_volume_to_postgres=lambda **_kwargs: [],
                ),
                skill_mgr=SimpleNamespace(
                    sync_local_skills_to_volume=lambda: 1,
                    build_catalog_xml=lambda: "<available_skills></available_skills>",
                ),
                context=None,
                engine=failing_engine,
                turn_lock=asyncio.Lock(),
            )

        async def get(self, _session_id: str) -> Any:
            return self.runtime

        async def close(self) -> None:
            return None

    @contextmanager
    def fake_span(**_kwargs: Any) -> Any:
        yield SimpleNamespace(
            trace_id="trace-failed-id",
            set_inputs=lambda _value: None,
            set_outputs=lambda _value: None,
            set_attributes=lambda _value: None,
        )

    @contextmanager
    def fake_dspy_context(**_kwargs: Any) -> Any:
        yield

    monkeypatch.setattr(server, "SessionRuntimeRegistry", RuntimeRegistry)
    monkeypatch.setattr(
        server, "dspy", SimpleNamespace(settings=SimpleNamespace(callbacks=[]), context=fake_dspy_context)
    )
    monkeypatch.setattr(server.mlflow, "start_span", fake_span)
    monkeypatch.setattr(server.mlflow, "update_current_trace", lambda **_kwargs: None)
    monkeypatch.setattr(server.mlflow, "set_experiment", lambda **_kwargs: None)
    monkeypatch.setattr(server.mlflow, "get_last_active_trace_id", lambda: "stale-previous-trace")
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)

    with TestClient(app) as client:
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "anything"})

    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    failure = next(event for event in events if event["event"] == "error")
    if fail_preparation:
        assert "trace_id" not in failure["payload"]
    else:
        assert failure["payload"]["trace_id"] == "trace-failed-id"
    assert "The Turn failed" in failure["payload"]["error"]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("files/a.log", "files/a.log"),
        ("/workspace/files/a.log", "files/a.log"),
        ("skills/x/SKILL.md", "skills/x/SKILL.md"),
    ],
)
def test_child_input_paths_accept_the_sandbox_absolute_form(given: str, expected: str) -> None:
    from fleet_rlm.sandbox.paths import _child_input_path

    assert _child_input_path(given) == expected


@pytest.mark.parametrize("bad", ["/workspace/artifacts/x", "/etc/passwd", "/workspace/files/../memory/x", "files/../x"])
def test_child_input_paths_still_reject_escapes_and_other_roots(bad: str) -> None:
    from fleet_rlm.sandbox.paths import _child_input_path

    with pytest.raises(ValueError):
        _child_input_path(bad)


def _usage_callback() -> tuple[Any, list[dict]]:
    import fleet_rlm.server as server

    events: list[dict] = []
    callback = server.SSERuntimeCallback(None, None)  # type: ignore[arg-type]
    callback._emit = events.append  # type: ignore[method-assign]
    return callback, events


def test_lm_end_event_reports_usage_from_dspys_tracker() -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    tracker = UsageTracker()
    lm = SimpleNamespace(model="openai/m")
    with dspy.context(usage_tracker=tracker):
        callback.on_lm_start("c1", lm, {})
        tracker.add_usage(
            "openai/m",
            {
                "prompt_tokens": 40,
                "completion_tokens": 900,
                "total_tokens": 940,
                "completion_tokens_details": {"reasoning_tokens": 850},
            },
        )
        callback.on_lm_end("c1", ["answer"], None)
    assert events[-1]["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 900,
        "total_tokens": 940,
        "reasoning_tokens": 850,
    }


def test_lm_end_event_streams_provider_reasoning_redacted() -> None:
    callback, events = _usage_callback()

    callback.on_lm_end("c2", [{"text": "42", "reasoning_content": "Plan: use api_key=hidden"}], None)
    assert events[-1]["reasoning"] == "Plan: use api_key=[redacted]"

    callback.on_lm_end("c3", ["42"], None)
    assert "reasoning" not in events[-1]


def test_lm_end_event_skips_usage_when_a_concurrent_call_shared_the_tracker() -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    tracker = UsageTracker()
    lm = SimpleNamespace(model="openai/m")
    with dspy.context(usage_tracker=tracker):
        callback.on_lm_start("c1", lm, {})
        callback.on_lm_start("c2", lm, {})
        tracker.add_usage("openai/m", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        tracker.add_usage("openai/m", {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        callback.on_lm_end("c1", ["a"], None)
        callback.on_lm_end("c2", ["b"], None)
    assert all("usage" not in event for event in events if event["event"] == "lm_end")


def test_lm_end_event_omits_usage_without_a_tracker_or_on_failure() -> None:
    callback, events = _usage_callback()
    callback.on_lm_start("c1", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c1", ["x"], None)
    callback.on_lm_start("c2", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c2", None, RuntimeError("boom"))
    assert all("usage" not in event for event in events if event["event"] == "lm_end")


def _engine_with_fake_children(monkeypatch, child_call):
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = AdaptiveFleetEngine(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
    )

    class FakeChild:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __call__(self, **kwargs):
            return SimpleNamespace(findings=child_call(**kwargs))

    monkeypatch.setattr(dspy, "RLM", FakeChild)
    return engine.primary_rlm._user_tools["spawn_agents"]


def test_spawn_agents_runs_children_concurrently_and_keeps_order(monkeypatch) -> None:
    import threading

    barrier = threading.Barrier(3, timeout=10)  # all three must be in flight at once, or this raises

    def child(subtask: str) -> str:
        barrier.wait()
        time.sleep({"a": 0.3, "b": 0.0, "c": 0.15}[subtask])  # finish out of order
        return f"done:{subtask}"

    tool = _engine_with_fake_children(monkeypatch, child)
    results = tool(subtasks=["a", "b", "c"])
    assert [r["findings"] for r in results] == ["done:a", "done:b", "done:c"]
    assert all(r["artifacts"] == [] for r in results)


def test_spawn_agents_returns_a_failing_child_as_a_value(monkeypatch) -> None:
    def child(subtask: str) -> str:
        if subtask == "bad":
            raise ValueError("Child input file is unavailable: files/x")
        return f"done:{subtask}"

    results = _engine_with_fake_children(monkeypatch, child)(subtasks=["ok", "bad", "ok2"])
    assert [r["findings"] for r in results] == ["done:ok", "", "done:ok2"]
    assert "unavailable" in results[1]["error"] and "error" not in results[0]


def test_spawn_agents_aborts_when_the_turn_budget_is_exhausted(monkeypatch) -> None:
    def child(**_kwargs) -> str:
        raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    with pytest.raises(RuntimeError, match="budget exhausted"):
        _engine_with_fake_children(monkeypatch, child)(subtasks=["a", "b"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subtasks": []},
        {"subtasks": ["a"] * 9},
        {"subtasks": ["a", 1]},
        {"subtasks": ["a", "b"], "input_paths": [["files/x"]]},
    ],
)
def test_spawn_agents_rejects_invalid_batches(monkeypatch, kwargs) -> None:
    tool = _engine_with_fake_children(monkeypatch, lambda subtask: subtask)
    with pytest.raises(ValueError):
        tool(**kwargs)


def test_spawn_agents_children_inherit_the_callers_context(monkeypatch) -> None:
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    marker.set("turn-context")
    tool = _engine_with_fake_children(monkeypatch, lambda **_kwargs: marker.get())
    assert [r["findings"] for r in tool(subtasks=["a", "b"])] == ["turn-context", "turn-context"]


def test_shutdown_flushes_pending_traces_when_tracing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.server as server

    flushed: list[bool] = []
    monkeypatch.setattr(server.mlflow, "flush_trace_async_logging", lambda: flushed.append(True))
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)
    with TestClient(app):
        app.state.mlflow_experiment_id = "experiment-test-id"
        assert flushed == []
    assert flushed == [True]


def test_shutdown_skips_the_trace_flush_when_tracing_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.server as server

    flushed: list[bool] = []
    monkeypatch.setattr(server.mlflow, "flush_trace_async_logging", lambda: flushed.append(True))
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)
    with TestClient(app):
        pass
    assert flushed == []
