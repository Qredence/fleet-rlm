"""Sandbox egress is blocked by default; the host bridge (llm_query, tools, replies) keeps working."""

from __future__ import annotations

import importlib.metadata
import inspect
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from daytona import CreateSandboxFromSnapshotParams, Daytona
from emulator import emulated_sandbox

from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

# Applied to every interpreter context of a "firewalled" emulated sandbox: any outbound socket fails,
# as Daytona's network_block_all does. Unix sockets and files (the bridge's reply path) still work.
_FIREWALL = r"""
import socket as _socket
def _blocked(*_args, **_kwargs):
    raise OSError(101, "Network is unreachable (network_block_all)")
_socket.create_connection = _blocked
_original_connect = _socket.socket.connect
def _connect(self, address):
    if self.family in (_socket.AF_INET, _socket.AF_INET6):
        _blocked()
    return _original_connect(self, address)
_socket.socket.connect = _connect
"""


class _FirewalledInterpreter:
    """Wrap the emulator so every new context starts behind the firewall."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create_context(self) -> Any:
        context = self._inner.create_context()
        assert self._inner.run_code(_FIREWALL, context=context, timeout=10).error is None
        return context


class _HostLM:
    """The sub LM runs on the host; its "network" is the host's, never the sandbox's."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str, **_kwargs: Any) -> list[str]:
        self.prompts.append(prompt)
        return [f"host answered: {prompt}"]


def _sdk_body(params: CreateSandboxFromSnapshotParams) -> Any:
    captured: list[Any] = []

    class _Stop(Exception):  # noqa: N818 - control flow only
        pass

    def create_sandbox(body: Any, **_kwargs: Any) -> Any:
        captured.append(body)
        raise _Stop

    client = SimpleNamespace(_target=None, _sandbox_api=SimpleNamespace(create_sandbox=create_sandbox))
    client._validate_language_label = lambda label: Daytona._validate_language_label(client, label)  # type: ignore[attr-defined]
    with pytest.raises(_Stop):
        Daytona._create(client, params, timeout=60)  # type: ignore[arg-type]
    return captured[0]


def _created_params(manager: DaytonaSandboxManager, sandbox: Any) -> list[CreateSandboxFromSnapshotParams]:
    created: list[CreateSandboxFromSnapshotParams] = []
    manager._client = SimpleNamespace(
        create=lambda params: created.append(params) or sandbox,
        list=lambda _query: [],
        stop=lambda *_a, **_k: None,
    )
    return created


def test_sdk_field_names_match_the_pinned_daytona() -> None:
    assert importlib.metadata.version("daytona") == "0.224.0"
    fields = CreateSandboxFromSnapshotParams.model_fields
    assert {"network_block_all", "domain_allow_list", "network_allow_list"} <= set(fields)
    assert fields["network_block_all"].annotation == (bool | None)
    assert fields["domain_allow_list"].annotation == (str | None)  # comma-separated, not a list


def test_settings_block_network_by_default_and_normalize_the_allow_list(monkeypatch) -> None:
    for name in ("FLEET_DAYTONA_NETWORK_BLOCK_ALL", "FLEET_DAYTONA_DOMAIN_ALLOW_LIST"):
        monkeypatch.delenv(name, raising=False)
    settings = FleetSettings()
    assert settings.daytona_network_block_all is True and settings.daytona_domain_allow_list == ""
    monkeypatch.setenv("FLEET_DAYTONA_NETWORK_BLOCK_ALL", "false")
    monkeypatch.setenv("FLEET_DAYTONA_DOMAIN_ALLOW_LIST", " Example.com, *.daytona.io ,,example.com")
    settings = FleetSettings()
    assert settings.daytona_network_block_all is False
    assert settings.daytona_domain_allow_list == "example.com,*.daytona.io"
    assert FleetSettings(daytona_domain_allow_list=["a.example", "b.example"]).daytona_domain_allow_list == (
        "a.example,b.example"
    )


@pytest.mark.parametrize(
    "entry", ["https://example.com", "example.com/path", "example.com:443", "10.0.0.1", "*", "exa mple.com"]
)
def test_settings_refuse_allow_list_entries_daytona_would_reject(entry: str) -> None:
    with pytest.raises(ValueError):
        FleetSettings(daytona_domain_allow_list=entry)
    with pytest.raises(ValueError):
        DaytonaSandboxManager("key", "org", "volume", "net", domain_allow_list=entry)


def test_settings_refuse_more_than_one_hundred_domains() -> None:
    with pytest.raises(ValueError, match="at most 100"):
        FleetSettings(daytona_domain_allow_list=[f"d{index}.example" for index in range(101)])


def test_checked_in_toml_blocks_network() -> None:
    import tomllib

    config = tomllib.loads((Path(__file__).parents[2] / "config/fleet.toml").read_text(encoding="utf-8"))
    assert config["daytona"]["network_block_all"] is True and config["daytona"]["domain_allow_list"] == ""


@pytest.mark.parametrize(
    ("block_all", "allow_list", "expected"),
    [
        (True, "", {"network_block_all": True}),
        (True, "example.com", {"domain_allow_list": "example.com"}),
        (False, "example.com", {"domain_allow_list": "example.com"}),
        (False, "", {}),
    ],
)
def test_network_params_send_at_most_one_daytona_firewall_option(block_all, allow_list, expected) -> None:
    root = DaytonaSandboxManager(
        "key", "org", "volume", "net", network_block_all=block_all, domain_allow_list=allow_list
    )
    assert root.network_params() == expected
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        body = _sdk_body(CreateSandboxFromSnapshotParams(snapshot="fleet-rlm-base", **root.network_params()))
    assert body.network_block_all == expected.get("network_block_all")
    assert body.domain_allow_list == expected.get("domain_allow_list")
    assert body.network_allow_list is None


def test_root_and_child_sandboxes_are_created_with_network_blocked(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    root = DaytonaSandboxManager("key", "org", "volume", "net")
    root_created = _created_params(root, SimpleNamespace(id="root-sandbox", state="started"))
    root.ensure_sandbox()
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_files", lambda _self, _paths: {})
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", lambda _self, _files: None)
    child_created: list[CreateSandboxFromSnapshotParams] = []
    original_init = DaytonaSandboxManager.__post_init__

    def capture_child_client(self: DaytonaSandboxManager) -> None:
        original_init(self)
        if self.parent is not None:
            self._client = SimpleNamespace(
                create=lambda params: child_created.append(params) or SimpleNamespace(id="child-sandbox"),
                list=lambda _query: [],
            )

    monkeypatch.setattr(DaytonaSandboxManager, "__post_init__", capture_child_client)
    child = root.allocate_child()
    assert child.network_block_all is True and child.domain_allow_list == ""
    for params in (root_created[0], child_created[0]):
        assert params.network_block_all is True
        assert params.domain_allow_list is None and params.network_allow_list is None


def test_bridged_llm_query_works_while_sandbox_egress_is_blocked(tmp_path) -> None:
    emulated = emulated_sandbox(tmp_path)
    firewalled = SimpleNamespace(
        id="firewalled",
        state="started",
        code_interpreter=_FirewalledInterpreter(emulated.code_interpreter),
        fs=emulated.fs,
    )
    host_lm = _HostLM()
    manager = DaytonaSandboxManager("key", "org", "volume", "net", sub_lm=host_lm)  # type: ignore[arg-type]
    created = _created_params(manager, firewalled)
    try:
        manager._sandbox = None
        interpreter = DaytonaInterpreter(manager)
        # Model code cannot reach the network from inside the sandbox ...
        egress = interpreter.execute(
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('pypi.org', 443), timeout=2)\n"
            "    egress = 'open'\n"
            "except OSError as exc:\n"
            "    egress = 'blocked: ' + str(exc)\n"
            "egress"
        )
        assert str(egress).startswith("blocked"), egress
        # ... but llm_query is a host call over the bridge: stdout marker out, reply file uploaded in.
        answer = interpreter.execute("llm_query('ping')")
        assert answer == "host answered: ping"
        assert host_lm.prompts == ["ping"]
        assert any(path.startswith("/tmp/fleet/replies/") for path in emulated.fs.uploads)
        assert created[0].network_block_all is True and created[0].domain_allow_list is None
    finally:
        emulated.code_interpreter.close()


@pytest.fixture(autouse=True)
def fresh_policy_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.sandbox import manager as manager_module

    monkeypatch.setattr(manager_module, "_NETWORK_POLICY_WARNED", False)


def _policy_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and "network policy" in record.getMessage()
    ]


def _create_root(monkeypatch, tmp_path, returned: Any, **settings: Any) -> DaytonaSandboxManager:
    monkeypatch.chdir(tmp_path)
    root = DaytonaSandboxManager("key", "org", "volume", "net", **settings)
    _created_params(root, returned)
    assert root.ensure_sandbox() is returned
    return root


def test_an_applied_block_all_policy_is_not_reported(monkeypatch, tmp_path, caplog) -> None:
    returned = SimpleNamespace(id="sb", state="started", network_block_all=True, domain_allow_list=None)
    _create_root(monkeypatch, tmp_path, returned)
    assert _policy_warnings(caplog) == []


def test_an_org_policy_overriding_block_all_warns_once_per_process(monkeypatch, tmp_path, caplog) -> None:
    # Tier 1/2: the organization policy wins and the sandbox does not report block-all.
    for name in ("first", "second"):
        returned = SimpleNamespace(id=name, state="started", network_block_all=False, domain_allow_list=None)
        _create_root(monkeypatch, tmp_path, returned)
    warnings_logged = _policy_warnings(caplog)
    assert len(warnings_logged) == 1
    assert "FLEET_DAYTONA_NETWORK_BLOCK_ALL" in warnings_logged[0]
    assert "Tier 3 or Tier 4" in warnings_logged[0] and "network_block_all=False" in warnings_logged[0]


def test_a_policy_missing_from_the_response_is_refreshed_before_judging(monkeypatch, tmp_path, caplog) -> None:
    returned = SimpleNamespace(id="sb", state="started", network_block_all=None, domain_allow_list=None, refreshes=0)

    def refresh_data() -> None:
        returned.refreshes += 1
        returned.network_block_all = True

    returned.refresh_data = refresh_data
    _create_root(monkeypatch, tmp_path, returned)
    assert returned.refreshes == 1 and _policy_warnings(caplog) == []


def test_an_unverifiable_policy_is_reported_never_silently_skipped(monkeypatch, tmp_path, caplog) -> None:
    _create_root(monkeypatch, tmp_path, SimpleNamespace(id="sb", state="started"))
    (message,) = _policy_warnings(caplog)
    assert "is not reported by Daytona" in message and "Tier 3 or Tier 4" in message


def test_a_mismatched_domain_allow_list_names_its_setting(monkeypatch, tmp_path, caplog) -> None:
    returned = SimpleNamespace(id="sb", state="started", network_block_all=None, domain_allow_list="other.example")
    _create_root(monkeypatch, tmp_path, returned, domain_allow_list="example.com")
    (message,) = _policy_warnings(caplog)
    assert "FLEET_DAYTONA_DOMAIN_ALLOW_LIST=example.com" in message


def test_a_matching_domain_allow_list_is_accepted_in_any_order_or_case(monkeypatch, tmp_path, caplog) -> None:
    returned = SimpleNamespace(
        id="sb", state="started", network_block_all=False, domain_allow_list="B.example, a.example"
    )
    _create_root(monkeypatch, tmp_path, returned, domain_allow_list="a.example,b.example")
    assert _policy_warnings(caplog) == []


def test_no_policy_requested_means_nothing_to_verify(monkeypatch, tmp_path, caplog) -> None:
    _create_root(monkeypatch, tmp_path, SimpleNamespace(id="sb", state="started"), network_block_all=False)
    assert _policy_warnings(caplog) == []


def test_the_sdk_sandbox_exposes_the_policy_fields_checked() -> None:
    from daytona import Sandbox
    from daytona_api_client import Sandbox as SandboxDto

    assert {"network_block_all", "domain_allow_list"} <= set(SandboxDto.model_fields)
    # The SDK copies both from the full SandboxDto (create/get responses, not list items) onto the handle.
    source = inspect.getsource(Sandbox)
    assert "self.network_block_all: bool | None = (" in source
    assert "self.domain_allow_list: str | None = sandbox_dto.domain_allow_list" in source
