from __future__ import annotations

import importlib
import sys
from types import ModuleType


def test_cli_package_defers_runner_import(monkeypatch) -> None:
    sys.modules.pop("fleet_rlm.cli", None)
    sys.modules.pop("fleet_rlm.cli.runners", None)

    cli_pkg = importlib.import_module("fleet_rlm.cli")

    assert "fleet_rlm.cli.runners" not in sys.modules

    sentinel = ModuleType("fleet_rlm.cli.runners")

    def fake_import_module(name: str) -> ModuleType:
        assert name == "fleet_rlm.cli.runners"
        sys.modules[name] = sentinel
        return sentinel

    monkeypatch.setattr(cli_pkg, "import_module", fake_import_module)

    runners_module = cli_pkg.runners

    assert runners_module is sentinel
    assert cli_pkg.runners is sentinel
