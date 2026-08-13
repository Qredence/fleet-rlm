from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.manage_prompts import (
    PromptRegistryError,
    build_parser,
    link_traces,
    list_prompts,
    main,
    register,
    set_alias,
)


class _FakePromptVersion:
    def __init__(self, name: str, version: int, template: str = "") -> None:
        self.name = name
        self.version = version
        self.template = template


class _FakePrompt:
    def __init__(self, name: str, tags: dict[str, str] | None = None) -> None:
        self.name = name
        self.tags = dict(tags or {})


class _FakeTrace:
    def __init__(self, trace_id: str) -> None:
        self.info = SimpleNamespace(trace_id=trace_id)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    traces: list[_FakeTrace] | None = None,
    prompts: list[_FakePrompt] | None = None,
) -> SimpleNamespace:
    monkeypatch.delenv("FLEET_MLFLOW_EXPERIMENT_NAME", raising=False)
    calls = SimpleNamespace(
        registered=[],
        aliases=[],
        linked=[],
        experiment_linked=[],
        text_file=None,
    )

    class _FakeClient:
        def _link_prompt_to_experiment(self, prompt_version: Any, experiment_id: str) -> None:
            calls.experiment_linked.append((getattr(prompt_version, "version", None), experiment_id))

        def get_prompt_version_by_alias(self, _name: str, _alias: str) -> _FakePromptVersion:
            return _FakePromptVersion(_name, version=3)

        def link_prompt_versions_to_trace(self, prompt_versions: list[Any], trace_id: str) -> None:
            calls.linked.append(([getattr(pv, "version", None) for pv in prompt_versions], trace_id))

    client_mod = ModuleType("mlflow.tracking.client")
    client_mod.MlflowClient = _FakeClient  # type: ignore[attr-defined]
    tracking_mod = ModuleType("mlflow.tracking")
    tracking_mod.client = client_mod  # type: ignore[attr-defined]

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_experiment_by_name = (  # type: ignore[attr-defined]
        lambda name: SimpleNamespace(experiment_id="exp-1") if name == "fleet-rlm" else None
    )

    def register_prompt(
        *,
        name: str,
        template: str,
        commit_message: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> _FakePromptVersion:
        calls.registered.append((name, template, commit_message, tags))
        return _FakePromptVersion(name, version=2, template=template)

    mlflow.register_prompt = register_prompt  # type: ignore[attr-defined]
    mlflow.set_prompt_alias = (  # type: ignore[attr-defined]
        lambda *, name, alias, version: calls.aliases.append((name, alias, version))
    )
    mlflow.load_prompt = (  # type: ignore[attr-defined]
        lambda name, version=None: _FakePromptVersion(name, version=version or 1, template="tpl")
    )
    mlflow.search_traces = lambda **_kwargs: list(traces or [])  # type: ignore[attr-defined]
    mlflow.search_prompts = lambda **_kwargs: list(prompts or [])  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking_mod)
    monkeypatch.setitem(sys.modules, "mlflow.tracking.client", client_mod)
    return calls


def _args(argv: list[str], tmp_path) -> object:
    return build_parser().parse_args([*argv, "--output", str(tmp_path / "receipt.json")])


def test_register_creates_version_links_experiment_and_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)
    source = tmp_path / "sig.txt"
    source.write_text("You are Fleet.", encoding="utf-8")

    receipt = register(_args(["register", "--text-file", str(source), "--experiment-id", "exp-1"], tmp_path))

    assert receipt["command"] == "register"
    assert receipt["version"] == 2
    assert receipt["template_chars"] == len("You are Fleet.")
    assert calls.registered[0][0] == "fleet-rlm-signature"
    assert calls.registered[0][1] == "You are Fleet."
    assert calls.registered[0][3]["fleet.signature_sha256"]
    assert calls.experiment_linked == [(2, "exp-1")]


def test_register_sets_alias_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)
    source = tmp_path / "sig.txt"
    source.write_text("You are Fleet.", encoding="utf-8")

    receipt = register(
        _args(["register", "--text-file", str(source), "--alias", "latest", "--experiment-id", "exp-1"], tmp_path)
    )

    assert receipt["prompt_alias"] == "latest"
    assert calls.aliases == [("fleet-rlm-signature", "latest", 2)]


def test_register_rejects_empty_template(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch)
    source = tmp_path / "sig.txt"
    source.write_text("   ", encoding="utf-8")

    with pytest.raises(PromptRegistryError, match="non-empty"):
        register(_args(["register", "--text-file", str(source), "--experiment-id", "exp-1"], tmp_path))


def test_link_traces_links_version_to_tagged_traces(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(
        monkeypatch,
        traces=[_FakeTrace("trace-a"), _FakeTrace("trace-b"), _FakeTrace("")],
    )

    receipt = link_traces(_args(["link-traces", "--version", "2", "--experiment-id", "exp-1"], tmp_path))

    assert receipt["traces_linked"] == 2
    assert receipt["traces_skipped"] == 1
    assert sorted(trace_id for _versions, trace_id in calls.linked) == ["trace-a", "trace-b"]
    assert calls.linked[0][0] == [2]


def test_link_traces_resolves_alias(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, traces=[_FakeTrace("trace-a")])

    receipt = link_traces(_args(["link-traces", "--alias", "latest", "--experiment-id", "exp-1"], tmp_path))

    assert receipt["version"] == 3
    assert calls.linked[0][0] == [3]


def test_link_traces_requires_version_or_alias(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch)
    with pytest.raises(PromptRegistryError, match="--version or --alias"):
        link_traces(_args(["link-traces", "--experiment-id", "exp-1"], tmp_path))


def test_list_returns_bounded_prompt_rows(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch, prompts=[_FakePrompt("fleet-rlm-signature", {"fleet.source": "signature"})])

    receipt = list_prompts(_args(["list"], tmp_path))

    assert receipt["count"] == 1
    assert receipt["prompts"][0]["name"] == "fleet-rlm-signature"
    assert receipt["prompts"][0]["tags"]["fleet.source"] == "signature"


def test_set_alias_requires_alias_and_version(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)

    receipt = set_alias(
        _args(["set-alias", "--alias", "champion", "--version", "2", "--experiment-id", "exp-1"], tmp_path)
    )

    assert receipt["alias"] == "champion"
    assert calls.aliases == [("fleet-rlm-signature", "champion", 2)]

    with pytest.raises(PromptRegistryError, match="--alias"):
        set_alias(_args(["set-alias", "--version", "2"], tmp_path))
    with pytest.raises(PromptRegistryError, match="--version"):
        set_alias(_args(["set-alias", "--alias", "champion"], tmp_path))


def test_commands_require_live(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    _install_fake_mlflow(monkeypatch)
    with pytest.raises(PromptRegistryError, match="FLEET_LIVE"):
        list_prompts(_args(["list"], tmp_path))
    with pytest.raises(PromptRegistryError, match="FLEET_LIVE"):
        set_alias(_args(["set-alias", "--alias", "latest", "--version", "1"], tmp_path))
    with pytest.raises(PromptRegistryError, match="FLEET_LIVE"):
        link_traces(_args(["link-traces", "--version", "1"], tmp_path))
    with pytest.raises(PromptRegistryError, match="FLEET_LIVE"):
        register(_args(["register", "--text-file", str(tmp_path / "missing.txt")], tmp_path))


def test_main_writes_failure_receipt_for_invalid_limit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["list", "--limit", "0", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    generated_at = payload.pop("generated_at")
    assert generated_at
    assert payload == {
        "schema": "fleet.prompt-registry/v1",
        "command": "list",
        "status": "failed",
        "error_category": "PromptRegistryError",
    }
