from __future__ import annotations

import hashlib
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.primitives.code_interpreter import CodeExecutionError
from fastapi.testclient import TestClient

from fleet_rlm.config import FleetSettings
from fleet_rlm.dspy_support import FleetRLMChatAdapter
from fleet_rlm.interpreter import (
    _DAEMON_CODE,
    DaytonaInterpreter,
    DaytonaSandboxManager,
    _workspace_relative_path,
    session_volume_subpath,
    validate_session_id,
)
from fleet_rlm.memory_and_eval import (
    SCHEMA_STATEMENTS,
    TRIAGE_ROUTE,
    AdaptiveFleetEngine,
    TurnTriageSignature,
    VolumePostgresHarness,
)
from fleet_rlm.server import create_app
from fleet_rlm.skills import SANDBOX_SKILL_HELPERS_CODE, SkillManager, parse_and_validate_skill_md
from fleet_rlm.workspace import WORKSPACE_CODE, WorkspaceDataset


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
    manager._daemon_ready = True

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


def test_daytona_manager_builds_syntax_valid_health_and_rpc_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    sandbox = SimpleNamespace(state=None, fs=SimpleNamespace(upload_file=lambda _content, _path: None))
    manager._client = object()
    manager._sandbox = sandbox
    remote_requests: list[str] = []

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str) -> str:
        assert sandbox_arg is sandbox
        compile(code, "<fleet-remote-rpc>", "exec")
        remote_requests.append(code)
        if "/health" in code:
            return json.dumps({"ok": True})
        if "/poll" in code:
            return json.dumps({"calls": []})
        return json.dumps({"ok": True})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.rpc("/poll") == {"calls": []}
    assert manager.rpc("/reply", {"id": "call-1", "value": "result"}) == {"ok": True}
    assert len(remote_requests) == 3
    assert "authorization='Bearer '+base64.b64decode(" in remote_requests[0]
    assert "data=body,headers=headers" in remote_requests[2]


def test_daytona_interpreter_returns_expression_results_and_execution_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {
            "stdout": "diagnostic output\n",
            "stderr": "",
            "final": None,
            "has_value": True,
            "value": '{"answer": 42}',
        },
        {"stdout": "", "stderr": "ValueError: broken", "final": None, "error": True},
    ]

    def rpc(path: str, _payload: dict[str, Any]) -> dict[str, Any]:
        assert path == "/exec"
        return responses.pop(0)

    interpreter = DaytonaInterpreter(SimpleNamespace(rpc=rpc))
    monkeypatch.setattr(interpreter, "start", lambda: None)

    assert interpreter.execute("json.dumps({'answer': 42})") == '{"answer": 42}'
    with pytest.raises(CodeExecutionError, match="ValueError: broken"):
        interpreter.execute("raise ValueError('broken')")


def test_daytona_host_calls_preserve_the_interpreter_dspy_context() -> None:
    lm = dspy.utils.DummyLM([])
    replies: list[dict[str, Any]] = []

    def rpc(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert path == "/reply"
        replies.append(payload)
        return {"ok": True}

    with dspy.context(lm=lm):
        interpreter = DaytonaInterpreter(
            SimpleNamespace(rpc=rpc),
            tools={"check_active_lm": lambda: dspy.settings.lm is lm},
        )

    context = interpreter._context.copy()
    thread = threading.Thread(
        target=context.run,
        args=(
            interpreter._handle_host_call,
            {"id": "call-1", "tool": "check_active_lm", "args": [], "kwargs": {}},
        ),
    )
    thread.start()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert replies == [{"id": "call-1", "value": True}]


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
            mlflow_tracking_uri="databricks",
            mlflow_trace_catalog="",
            mlflow_trace_schema="",
            mlflow_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_tracking_uri="databricks",
        mlflow_experiment_name="/Shared/fleet-rlm",
        mlflow_trace_catalog="fleet",
        mlflow_trace_schema="traces",
        mlflow_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.memory_and_eval as memory_and_eval

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
    import fleet_rlm.memory_and_eval as memory_and_eval

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

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.memory_and_eval"):
        prediction = engine(
            workspace_manifest="",
            memory_summary="",
            task="test fallback",
        )

    assert prediction.route == "deep_rlm"
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
    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="databricks/databricks-claude-sonnet-5.5",
        fleet_sub_model="openai/system.ai.claude-haiku-5-5",
        fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
        databricks_host="workspace.example.com",
        databricks_token="test-token",
    )

    with pytest.raises(ValueError, match="refusing to fall back"):
        settings.build_lms()

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

    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="openai/system.ai.claude-haiku-5-5",
        fleet_llm_base_url="http://gateway.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
    )
    with pytest.raises(ValueError, match="HTTPS workspace URL"):
        settings.build_lms()


def test_fleet_prefixed_mlflow_environment_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACKING_URI", "sqlite:///fleet-test.db")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)

    assert settings.mlflow_tracking_uri == "sqlite:///fleet-test.db"
    assert settings.mlflow_experiment_name == "fleet-test"


def test_lite_postgres_tables_do_not_collide_with_legacy_fleet_schema() -> None:
    session_schema, vfs_schema = SCHEMA_STATEMENTS[:2]

    assert "CREATE TABLE IF NOT EXISTS fleet_lite_sessions" in session_schema
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_vfs_files" in vfs_schema
    assert "REFERENCES fleet_lite_sessions(session_id)" in vfs_schema
    assert "CREATE TABLE IF NOT EXISTS fleet_sessions" not in session_schema


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


def test_triage_signature_retains_typed_choice_criteria() -> None:
    route_field = TurnTriageSignature.model_fields["route"]
    decision = TRIAGE_ROUTE(value="delegated_rlm", confidence=0.91)

    assert route_field.annotation is TRIAGE_ROUTE
    assert route_field.json_schema_extra is not None
    assert "direct_cot" in route_field.json_schema_extra["desc"]
    assert decision.value == "delegated_rlm"
    assert TRIAGE_ROUTE.criteria() == {
        "direct_cot": "Simple response requiring no code, files, or Skills",
        "single_rlm": "A task needing one bounded Python or Skill workflow",
        "delegated_rlm": "A multi-part task requiring an independent child investigation",
    }


def test_turn_sse_persists_and_returns_one_mocked_session_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.server as server

    files = {"files/input.txt": b"initial input", "files/rates.csv": b"rate\n3\n"}
    records = []

    def run_engine(**_kwargs):
        files["files/input.txt"] = b"modified during execution"
        return SimpleNamespace(answer="processed locally", route="single_rlm", learned_skill_note="")

    def persist(**kwargs):
        records.append(kwargs["turn_record"])
        return ["artifacts/result.csv"]

    class RuntimeRegistry:
        def __init__(self, _settings: FleetSettings, _lms: tuple[Any, Any, Any, Any | None]):
            self.runtime = SimpleNamespace(
                sandbox_mgr=SimpleNamespace(
                    list_workspace_files=lambda: list(reversed(files)),
                    read_workspace_file=files.__getitem__,
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
        yield SimpleNamespace(set_inputs=lambda _value: None, set_outputs=lambda _value: None)

    global_callback = object()
    observed_callbacks: list[Any] = []

    @contextmanager
    def fake_dspy_context(*, callbacks: list[Any], disable_history: bool) -> Any:
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
    monkeypatch.setattr(server.mlflow, "update_current_trace", lambda **_kwargs: None)
    monkeypatch.setattr(server.mlflow, "set_experiment", lambda **_kwargs: None)
    monkeypatch.setattr(server.mlflow, "get_last_active_trace_id", lambda: "trace-test-id")
    app = create_app(settings=_unity_gateway_test_settings(), initialize_db=False, configure_observability=False)

    with TestClient(app) as client:
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "summarize this file"})

    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert events[0]["event"] == "turn_status"
    assert events[0]["payload"] == {"phase": "preparation"}
    assert events[1]["event"] == "turn_complete"
    assert events[1]["payload"] == {
        "answer": "processed locally",
        "route": "single_rlm",
        "learned_skill_note": "",
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
    # The server uses "done" as its internal queue sentinel; the HTTP stream
    # ends after it and deliberately exposes only user-facing events.
    assert len(events) == 2


@pytest.fixture
def interpreter_daemon(tmp_path: Path) -> tuple[str, str, subprocess.Popen[bytes]]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = "test-interpreter-token"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    code = (
        (
            SANDBOX_SKILL_HELPERS_CODE
            + "\n"
            + WORKSPACE_CODE
            + "\nTOKEN = "
            + repr(token)
            + "\n"
            + _DAEMON_CODE.replace("TOKEN = __FLEET_TOKEN__\n", "")
        )
        .replace("8765", str(port))
        .replace("/workspace", str(workspace_root))
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("Interpreter daemon exited during startup")
        try:
            _daemon_request(base_url, token, "/health")
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.025)
    else:
        process.terminate()
        process.wait(timeout=5)
        pytest.fail("Interpreter daemon did not become ready")
    try:
        yield base_url, token, process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _daemon_request(
    base_url: str,
    token: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def _poll_for_call(base_url: str, token: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        calls = _daemon_request(base_url, token, "/poll")["calls"]
        if calls:
            return calls[0]
    raise AssertionError("Interpreter did not publish a host call")


def test_daemon_is_auth_guarded_and_isolates_parallel_namespace_output(
    interpreter_daemon: tuple[str, str, subprocess.Popen[bytes]],
) -> None:
    base_url, token, _ = interpreter_daemon
    request = urllib.request.Request(base_url + "/health")
    with pytest.raises(urllib.error.HTTPError) as unauthorized:
        urllib.request.urlopen(request, timeout=3)
    assert unauthorized.value.code == 401

    responses: dict[str, dict[str, object]] = {}

    def execute(ns_id: str, marker: str) -> None:
        responses[marker] = _daemon_request(
            base_url,
            token,
            "/exec",
            {"ns_id": ns_id, "code": f"import time\ntime.sleep(0.1)\nprint({marker!r})"},
        )

    threads = [
        threading.Thread(target=execute, args=("ns_00000001", "alpha")),
        threading.Thread(target=execute, args=("ns_00000002", "beta")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert responses["alpha"]["stdout"] == "alpha\n"
    assert responses["beta"]["stdout"] == "beta\n"


def test_daemon_preserves_tool_bindings_and_completes_host_bridge_round_trip(
    interpreter_daemon: tuple[str, str, subprocess.Popen[bytes]],
) -> None:
    base_url, token, _ = interpreter_daemon
    result: dict[str, object] = {}
    # Accelerate any finite condition deadline so a slow reply regression takes
    # milliseconds, not two minutes. An unbounded wait still awaits the reply.
    _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000003",
            "code": (
                "import threading\n"
                "original_wait_for = threading.Condition.wait_for\n"
                "def accelerated_wait_for(self, predicate, timeout=None):\n"
                "    return original_wait_for(self, predicate, None if timeout is None else min(timeout, 0.05))\n"
                "threading.Condition.wait_for = accelerated_wait_for"
            ),
        },
    )

    def execute() -> None:
        result.update(
            _daemon_request(
                base_url,
                token,
                "/exec",
                {
                    "ns_id": "ns_00000003",
                    "tools": ["sample_tool"],
                    "variables": {"sample_tool": "overwritten", "SUBMIT": "overwritten"},
                    "code": (
                        "print(callable(sample_tool))\nprint(SUBMIT is not None)\n"
                        "print(sample_tool.__name__)\nprint(sample_tool('arg'))"
                    ),
                },
            )
        )

    thread = threading.Thread(target=execute)
    thread.start()
    call = _poll_for_call(base_url, token)
    assert call["tool"] == "sample_tool"
    assert call["args"] == ["arg"]
    time.sleep(0.15)
    _daemon_request(base_url, token, "/reply", {"id": call["id"], "value": "bridged result"})
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert result["stdout"] == "True\nTrue\nsample_tool\nbridged result\n"


def test_daemon_accepts_registered_dspy_builtin_tools_without_overwriting_them(
    interpreter_daemon: tuple[str, str, subprocess.Popen[bytes]],
) -> None:
    base_url, token, _ = interpreter_daemon
    result = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000007",
            "tools": ["sub_rlm", "llm_query", "llm_query_batched"],
            "code": ("all(callable(tool) for tool in (sub_rlm, llm_query, llm_query_batched))"),
        },
    )

    assert result["has_value"] is True
    assert result["value"] is True


def test_daemon_returns_the_value_of_the_last_expression_for_flex_bridge(
    interpreter_daemon: tuple[str, str, subprocess.Popen[bytes]],
) -> None:
    base_url, token, _ = interpreter_daemon
    result = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000006",
            "variables": {"value": 42},
            "code": "import json\nprint('captured')\njson.dumps({'answer': value})",
        },
    )

    assert result["stdout"] == "captured\n"
    assert result["has_value"] is True
    assert result["value"] == '{"answer": 42}'


def test_daemon_drop_namespace_cancels_inflight_host_calls_and_bounds_output(
    interpreter_daemon: tuple[str, str, subprocess.Popen[bytes]],
) -> None:
    base_url, token, _ = interpreter_daemon
    result: dict[str, object] = {}

    def execute() -> None:
        result.update(
            _daemon_request(
                base_url,
                token,
                "/exec",
                {"ns_id": "ns_00000004", "code": "llm_query('pending')"},
            )
        )

    thread = threading.Thread(target=execute)
    thread.start()
    call = _poll_for_call(base_url, token)
    assert call["tool"] == "llm_query"
    _daemon_request(base_url, token, "/drop_ns", {"ns_id": "ns_00000004"})
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert result["error"] is True
    assert "namespace was closed" in str(result["stderr"])

    output = _daemon_request(
        base_url,
        token,
        "/exec",
        {"ns_id": "ns_00000005", "code": "print('x' * 150000)"},
    )
    assert output["truncated"] is True
    assert len(output["stdout"]) == 100_000


def test_workspace_dataset_preserves_revisions_and_rejects_symlink_escape(interpreter_daemon) -> None:
    base_url, token, _ = interpreter_daemon
    setup = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000006",
            "code": "workspace.write('files/data.csv', 'id,value\\n1,-7\\n2,11\\n3,5\\n')\n"
            "view = WorkspaceDatasetView(workspace.stat('files/data.csv'))\n"
            "[len(list(view.lines())), view.search('-7'), len(view.preview(4))]",
        },
    )
    assert not setup.get("error")
    assert setup["value"] == [4, [[2, "1,-7\n"]], 4]
    updated = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000006",
            "variables": {"replacement": "changed"},
            "code": "metadata = workspace.stat('files/data.csv')\n"
            "workspace.write('files/data.csv', replacement)\nWorkspaceDatasetView(metadata)",
        },
    )
    assert updated["error"] is True and "dataset source changed" in updated["stderr"]
    rejected = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000006",
            "code": "import os\nos.symlink('/etc/hostname', workspace.resolve('files/link'))\n"
            "workspace.read_text('files/link')",
        },
    )
    assert rejected["error"] is True and "symbolic links" in rejected["stderr"]


def test_namespace_imports_do_not_switch_to_another_agents_facade(interpreter_daemon) -> None:
    base_url, token, _ = interpreter_daemon
    for namespace, marker in [("ns_00000007", "left"), ("ns_00000008", "right")]:
        response = _daemon_request(
            base_url,
            token,
            "/exec",
            {
                "ns_id": namespace,
                "code": "import sys, types\ndspy = types.ModuleType('dspy')\n"
                f"dspy.marker = {marker!r}\nsys.modules['dspy'] = dspy",
            },
        )
        assert not response.get("error")
    response = _daemon_request(
        base_url,
        token,
        "/exec",
        {
            "ns_id": "ns_00000007",
            "code": "import dspy\ndspy.marker",
        },
    )
    assert response["value"] == "left"


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
    manager._daemon_ready = True
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


def test_bridge_and_execution_events_show_evidence_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from dspy.primitives.facade import SHIM_SETUP

    events: list[dict[str, Any]] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "activity-test", event_callback=events.append)
    interpreter = DaytonaInterpreter(manager, tools={"lookup": lambda: {"value": 17, "password": "hidden"}})
    monkeypatch.setattr(interpreter, "start", lambda: None)

    def rpc(_manager: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path == "/exec" and payload["code"] == "timeout()":
            raise TimeoutError("secret-detail")
        return {"has_value": True, "value": 17, "stdout": "api_key=hidden\n"}

    monkeypatch.setattr(DaytonaSandboxManager, "rpc", rpc)
    interpreter._handle_host_call({"id": "bridge-17", "tool": "lookup", "args": [], "kwargs": {}})
    interpreter._handle_host_call({"id": "bridge-18", "tool": "missing", "args": [], "kwargs": {}})
    assert interpreter.execute(SHIM_SETUP) == 17
    assert interpreter.execute("17") == 17
    with pytest.raises(TimeoutError):
        interpreter.execute("timeout()")
    assert "hidden" not in json.dumps(events)
    assert "secret-detail" not in json.dumps(events)
    tool_ends = [e for e in events if e["event"] == "tool_end"]
    assert [(e["call_id"], e["ok"]) for e in tool_ends] == [("bridge-17", True), ("bridge-18", False)]
    assert '"value": 17' in tool_ends[0]["output"]
    outputs = [e for e in events if e["event"] == "repl_output"]
    assert [e["phase"] for e in outputs] == ["bootstrap", "task", "task"]
    assert outputs[-1]["error_type"] == "TimeoutError"
    assert outputs[-1]["error"] is True
    for output in outputs:
        assert any(e["event"] == "repl_code" and e["execution_id"] == output["execution_id"] for e in events)
        assert output["duration_ms"] >= 0
        assert output["agent_id"] == manager.agent_id
