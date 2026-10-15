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
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.adapters.chat_adapter import ChatAdapter
from dspy.primitives.code_interpreter import CodeExecutionError
from dspy.utils.exceptions import AdapterParseError
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
    AdaptiveFleetEngine,
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
    sandbox = SimpleNamespace(state=None, fs=SimpleNamespace(upload_file=lambda _content, _path, **_kwargs: None))
    manager._client = object()
    manager._sandbox = sandbox
    remote_requests: list[str] = []

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str, **_kwargs) -> str:
        assert sandbox_arg is sandbox
        compile(code, "<fleet-remote-rpc>", "exec")
        remote_requests.append(code)
        if "/health" in code:
            return json.dumps({"ok": True, "protocol": 2, "instance": "test-daemon"})
        if "/poll" in code:
            return json.dumps({"calls": []})
        return json.dumps({"ok": True})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.rpc("/poll") == {"calls": []}
    assert manager.rpc("/reply", {"id": "call-1", "value": "result"}) == {"ok": True}
    assert len(remote_requests) == 3
    assert "authorization='Bearer '+base64.b64decode(" in remote_requests[0]
    assert "data=body,headers=" in remote_requests[2]


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


@contextmanager
def _recording_span(recorded: list[dict[str, Any]], name: str, span_type: str):
    span: dict[str, Any] = {"name": name, "span_type": span_type, "attributes": {}, "open": True}
    recorded.append(span)
    try:
        yield SimpleNamespace(
            set_inputs=lambda value: span.__setitem__("inputs", value),
            set_outputs=lambda value: span.__setitem__("outputs", value),
            set_attributes=span["attributes"].update,
            set_attribute=lambda key, value: span["attributes"].__setitem__(key, value),
        )
    finally:
        span["open"] = False


def test_daytona_exec_is_recorded_as_a_sandbox_tool_span(monkeypatch: pytest.MonkeyPatch) -> None:
    from mlflow.entities import SpanType

    import fleet_rlm.interpreter as interpreter_module

    spans: list[dict[str, Any]] = []
    open_during_rpc: list[bool] = []
    outcomes: list[Any] = [
        {"stdout": "", "stderr": "", "final": None, "value": "17", "error": False},
        RuntimeError("sandbox dropped"),
    ]

    def rpc(path: str, _payload: dict[str, Any]) -> dict[str, Any]:
        assert path == "/exec"
        open_during_rpc.append(spans[-1]["open"])
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    interpreter = DaytonaInterpreter(SimpleNamespace(rpc=rpc))
    monkeypatch.setattr(interpreter, "start", lambda: None)
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: _recording_span(spans, name, span_type)
    )

    interpreter.execute("17")
    with pytest.raises(RuntimeError, match="sandbox dropped"):
        interpreter.execute("17")

    assert [(span["name"], span["span_type"], span["open"]) for span in spans] == [
        ("sandbox.exec", SpanType.TOOL, False),
        ("sandbox.exec", SpanType.TOOL, False),
    ]
    assert open_during_rpc == [True, True]
    assert {"phase", "namespace", "execution_id", "host_calls"} <= set(spans[0]["attributes"])
    assert spans[0]["attributes"]["host_calls"] == 0
    assert spans[0]["attributes"]["phase"] == "task"
    assert spans[0]["attributes"]["namespace"] == interpreter.ns_id


def test_daytona_exec_span_counts_host_calls_made_during_the_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.interpreter as interpreter_module

    spans: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    def rpc(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path == "/reply":
            replies.append(payload)
            return {"ok": True}
        assert path == "/exec"
        interpreter._handle_host_call({"id": "call-1", "tool": "lookup", "args": [], "kwargs": {}})
        interpreter._handle_host_call({"id": "call-2", "tool": "lookup", "args": [], "kwargs": {}})
        return {"stdout": "", "stderr": "", "final": None, "value": "17", "error": False}

    interpreter = DaytonaInterpreter(SimpleNamespace(rpc=rpc), tools={"lookup": lambda: 17})
    monkeypatch.setattr(interpreter, "start", lambda: None)
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: _recording_span(spans, name, span_type)
    )

    interpreter.execute("lookup()")

    assert len(replies) == 2
    assert spans[0]["attributes"]["host_calls"] == 2


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

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.memory_and_eval"):
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
        def __init__(self, _settings: FleetSettings, _lms: tuple[Any, Any, Any, Any | None]):
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
    if path == "/exec":
        identity = {
            "execution_id": uuid.uuid4().hex,
            "ns_id": payload["ns_id"],
            "instance": _daemon_request(base_url, token, "/health")["instance"],
        }
        _daemon_request(base_url, token, "/exec/submit", {**payload, **identity})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = _daemon_request(base_url, token, "/exec/status", identity)
            if result["state"] == "completed":
                return result["result"]
            if result["state"] == "missing":
                return {"error": True, "stderr": "Interpreter namespace was closed"}
            time.sleep(0.005)
        raise TimeoutError("test execution did not finish")
    if path == "/exec/submit":
        path = "/exec"
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
        def __init__(self, _settings: FleetSettings, _lms: tuple[Any, Any, Any, Any | None]):
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
    from fleet_rlm.interpreter import _child_input_path

    assert _child_input_path(given) == expected


@pytest.mark.parametrize("bad", ["/workspace/artifacts/x", "/etc/passwd", "/workspace/files/../memory/x", "files/../x"])
def test_child_input_paths_still_reject_escapes_and_other_roots(bad: str) -> None:
    from fleet_rlm.interpreter import _child_input_path

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
    import time

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


def test_execution_submission_deduplicates_and_rejects_conflicts(interpreter_daemon):
    base, token, _ = interpreter_daemon
    identity = {
        "execution_id": uuid.uuid4().hex,
        "ns_id": "ns_00000001",
        "instance": _daemon_request(base, token, "/health")["instance"],
    }
    payload = {**identity, "code": "import time\ntime.sleep(.05)\ncounter = globals().get('counter', 0) + 1\ncounter"}
    for _ in range(2):
        _daemon_request(base, token, "/exec/submit", payload)
    with pytest.raises(urllib.error.HTTPError) as conflict:
        _daemon_request(base, token, "/exec/submit", {**payload, "code": "counter += 1"})
    assert conflict.value.code == 409
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = _daemon_request(base, token, "/exec/status", identity)
        if result["state"] == "completed":
            break
        time.sleep(0.01)
    assert result["result"]["value"] == 1
    _daemon_request(base, token, "/drop_ns", {"ns_id": identity["ns_id"]})
    assert _daemon_request(base, token, "/exec/status", identity)["state"] == "missing"


def test_lost_submission_response_recovers_one_side_effect(interpreter_daemon, monkeypatch):
    base, token, _ = interpreter_daemon
    manager = DaytonaSandboxManager("key", "org", "volume", "reconcile")
    manager._daemon_instance = _daemon_request(base, token, "/health")["instance"]
    calls = []

    def control(_self, path, payload):
        calls.append(path)
        result = _daemon_request(base, token, "/exec/submit" if path == "/exec" else path, payload)
        if path == "/exec":
            raise OSError("submission accepted but response lost")
        return result

    monkeypatch.setattr(DaytonaSandboxManager, "_rpc", control)
    result = manager.execute_remote(
        {
            "execution_id": uuid.uuid4().hex,
            "ns_id": "ns_00000001",
            "code": "counter = globals().get('counter', 0) + 1\ncounter",
        }
    )
    assert result["value"] == 1
    assert result["transport_retries"] == 1
    assert calls.count("/exec") == 1
    assert not manager._cancelled.is_set()


@pytest.mark.parametrize("failure", ["missing", "changed", "unavailable", "invalid"])
def test_uncertain_execution_stops_turn_without_replay(monkeypatch, failure):
    from fleet_rlm.interpreter import ExecutionOutcomeUnknownError

    manager = DaytonaSandboxManager("key", "org", "volume", "uncertain")
    manager._daemon_instance = "original"
    calls = []

    def control(_self, path, _payload):
        calls.append(path)
        if path == "/exec":
            return {"state": "running", "instance": "original"}
        if failure == "unavailable":
            raise OSError("gateway unavailable")
        return {
            "state": "missing" if failure == "missing" else "running" if failure == "changed" else "invalid",
            "instance": "changed" if failure == "changed" else "original",
        }

    monkeypatch.setattr(DaytonaSandboxManager, "_rpc", control)
    monkeypatch.setattr("fleet_rlm.interpreter._EXEC_POLL_INITIAL", 0.001)
    with pytest.raises(ExecutionOutcomeUnknownError):
        manager.execute_remote({"execution_id": uuid.uuid4().hex, "ns_id": "ns_00000001", "code": "42"})
    assert manager._cancelled.is_set()
    assert calls.count("/exec") == 1


def test_unaccepted_submission_can_retry_same_identity_once(monkeypatch):
    manager = DaytonaSandboxManager("key", "org", "volume", "resubmit")
    manager._daemon_instance = "original"
    submitted = []
    status_calls = 0

    def control(_self, path, payload):
        nonlocal status_calls
        if path == "/exec":
            submitted.append(payload.copy())
            if len(submitted) == 1:
                raise OSError("not accepted")
            return {"state": "running", "instance": "original"}
        status_calls += 1
        return {
            "instance": "original",
            "state": "missing" if status_calls == 1 else "completed",
            "result": {"value": 42},
        }

    monkeypatch.setattr(DaytonaSandboxManager, "_rpc", control)
    monkeypatch.setattr("fleet_rlm.interpreter._EXEC_POLL_INITIAL", 0.001)
    assert (
        manager.execute_remote({"execution_id": uuid.uuid4().hex, "ns_id": "ns_00000001", "code": "42"})["value"] == 42
    )
    assert len(submitted) == 2 and submitted[0] == submitted[1]


def test_actual_execution_span_marks_remote_code_error(tmp_path):
    # Run with a fresh MLflow exporter: earlier tests intentionally configure global callbacks.
    import os

    code = """
import mlflow
from types import SimpleNamespace
from fleet_rlm.interpreter import DaytonaInterpreter
from dspy.primitives.code_interpreter import CodeExecutionError
mlflow.set_experiment('execution-test')
interpreter = DaytonaInterpreter(SimpleNamespace(rpc=lambda *a: {
    'error': True, 'error_type': 'ValueError', 'stderr': 'ValueError: verification_probe'}))
interpreter.start = lambda: None
with mlflow.start_span(name='execution-test') as root:
    try:
        interpreter.execute("raise ValueError('verification_probe')")
    except CodeExecutionError:
        pass
    else:
        raise AssertionError('remote exception was not raised')
    trace_id = root.trace_id
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
execution = next(s for s in trace.data.spans if s.name == 'sandbox.exec')
assert execution.status.status_code.name == 'ERROR'
assert execution.outputs['error'] is True
assert execution.attributes['remote_error_type'] == 'ValueError'
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'traces.db'}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
        },
    )
    assert result.returncode == 0, result.stderr


def test_transient_status_failure_recovers_without_submission_retry(monkeypatch):
    manager = DaytonaSandboxManager("key", "org", "volume", "status-recovery")
    manager._daemon_instance = "original"
    paths = []

    def control(_self, path, _payload):
        paths.append(path)
        if path == "/exec":
            return {"instance": "original", "state": "running"}
        if paths.count("/exec/status") == 1:
            raise OSError("temporary gateway failure")
        return {"instance": "original", "state": "completed", "result": {"value": 42}}

    monkeypatch.setattr(DaytonaSandboxManager, "_rpc", control)
    monkeypatch.setattr("fleet_rlm.interpreter._EXEC_POLL_INITIAL", 0.001)
    result = manager.execute_remote({"execution_id": uuid.uuid4().hex, "ns_id": "ns_00000001", "code": "42"})
    assert result["value"] == 42 and result["transport_retries"] == 1
    assert paths.count("/exec") == 1


def test_daemon_rejects_wrong_instance_and_bounds_registry(interpreter_daemon):
    base, token, _ = interpreter_daemon
    instance = _daemon_request(base, token, "/health")["instance"]
    payload = {"instance": "wrong", "execution_id": uuid.uuid4().hex, "ns_id": "ns_00000001", "code": "42"}
    with pytest.raises(urllib.error.HTTPError) as mismatch:
        _daemon_request(base, token, "/exec/submit", payload)
    assert mismatch.value.code == 409
    # Shrink only the test daemon's private limit rather than creating 64 workers.
    _daemon_request(
        base, token, "/exec", {"ns_id": "ns_00000001", "code": "import __main__\n__main__.MAX_EXECUTIONS = 1"}
    )
    with pytest.raises(urllib.error.HTTPError) as full:
        _daemon_request(base, token, "/exec/submit", {**payload, "instance": instance})
    assert full.value.code == 429


def test_execution_deadline_prevents_submission(monkeypatch):
    manager = DaytonaSandboxManager("key", "org", "volume", "expired")
    manager._deadline = time.monotonic() - 1
    calls = []
    monkeypatch.setattr(DaytonaSandboxManager, "_rpc", lambda _self, *args: calls.append(args))
    with pytest.raises(TimeoutError, match="deadline"):
        manager.execute_remote({"execution_id": uuid.uuid4().hex, "ns_id": "ns_00000001", "code": "42"})
    assert calls == []
    assert manager._cancelled.is_set()
