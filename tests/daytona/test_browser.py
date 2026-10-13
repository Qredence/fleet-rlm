"""Native browser launch identity survives failed tools and crashed leaders."""

import json
import shlex
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona.browser import BrowserUnavailableError, RunBrowser


def browser_owner(exec_command, *, authorized=lambda: True):
    tainted = []
    owner = RunBrowser(
        SimpleNamespace(process=SimpleNamespace(exec=exec_command)),
        f"/tmp/fleet/{uuid4()}",
        time.monotonic() + 60,
        authorized,
        lambda: tainted.append(True),
    )
    return owner, tainted


def launch_result(scratch):
    return SimpleNamespace(
        exit_code=0,
        result=json.dumps(
            {
                "session": f"fleet-{scratch.rsplit('/', 1)[-1]}",
                "cdp_url": "http://127.0.0.1:9222",
                "process_groups": {"100": "123"},
            }
        ),
    )


def test_native_owner_launches_once_and_retains_groups_outside_model_binding():
    calls = []

    def execute(command, *, timeout):
        assert type(timeout) is int  # Daytona's generated SDK uses StrictInt.
        args = shlex.split(command)
        calls.append((args, timeout))
        if args[2] == "start":
            return launch_result(args[3])
        assert json.loads(args[5]) == {"100": "123"}
        return SimpleNamespace(exit_code=0, result="fleet-browser-cleanup-ok\n")

    owner, tainted = browser_owner(execute)
    owner.close()  # A URL-free Turn incurs no browser RPC or daemon launch.
    assert calls == []
    binding = owner.open_browser()
    assert owner.open_browser() == binding
    assert "process_groups" not in binding
    assert binding["environment"] == {"FLEET_BROWSER_RUN": owner.scratch.rsplit("/", 1)[-1]}
    owner.close()
    owner.close()
    assert [args[2] for args, _timeout in calls] == ["start", "close"]
    assert tainted == []
    assert owner.event_views()["open_browser"].output(binding) == {}


@pytest.mark.parametrize("failure", ["transport", "invalid_identity"])
def test_ambiguous_launch_taints_root_and_refuses_reuse(failure):
    calls = []

    def execute(command, **_kwargs):
        calls.append(command)
        if failure == "transport":
            raise RuntimeError("private provider details")
        return SimpleNamespace(exit_code=0, result='{"process_groups": {}}')

    owner, tainted = browser_owner(execute)
    with pytest.raises(BrowserUnavailableError, match="failed safely"):
        owner.open_browser()
    with pytest.raises(BrowserUnavailableError, match="unresolved"):
        owner.open_browser()
    with pytest.raises(BrowserUnavailableError, match="unresolved"):
        owner.close()
    assert len(calls) == 1
    assert tainted


def test_revocation_during_launch_retains_identity_for_cleanup():
    authorized = True
    calls = []

    def execute(command, **_kwargs):
        nonlocal authorized
        args = shlex.split(command)
        calls.append(args)
        if args[2] == "start":
            authorized = False
            return launch_result(args[3])
        assert json.loads(args[5]) == {"100": "123"}
        return SimpleNamespace(exit_code=0, result="fleet-browser-cleanup-ok")

    owner, tainted = browser_owner(execute, authorized=lambda: authorized)
    with pytest.raises(BrowserUnavailableError):
        owner.open_browser()
    owner.close()
    assert [args[2] for args in calls] == ["start", "close"]
    assert tainted


def test_unconfirmed_cleanup_taints_root_and_keeps_identity_for_retry():
    closes = []

    def execute(command, **_kwargs):
        args = shlex.split(command)
        if args[2] == "start":
            return launch_result(args[3])
        closes.append(json.loads(args[5]))
        return SimpleNamespace(exit_code=1, result="private diagnostics")

    owner, tainted = browser_owner(execute)
    owner.open_browser()
    for _attempt in range(2):
        with pytest.raises(BrowserUnavailableError, match="failed safely"):
            owner.close()
    assert closes == [{"100": "123"}, {"100": "123"}]
    assert tainted
