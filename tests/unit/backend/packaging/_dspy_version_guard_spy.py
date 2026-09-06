"""Private subprocess spy lanes for the exact-3.3.1 runtime DSPy guard.

This module is private test-lane instrumentation only; it is never imported by
production code. Each invocation injects a reported ``dspy.__version__``,
installs ordered spies on the production provider/database/Daytona
resource-construction seams, drives one public startup or composition entry
point, and prints one bounded JSON record as the final stdout line.

Usage::

    uv run python tests/unit/backend/packaging/_dspy_version_guard_spy.py <mode> <reported-version>

Exit codes: ``0`` when the guard accepts the runtime, ``3`` when the guard
rejects it fail-closed, ``2`` for any unexpected harness or composition error.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from typing import Any

_GUARD_REJECTED = 3
_HARNESS_ERROR = 2

_RESOURCE_LABELS = ("database", "daytona", "provider")
_COUNTER_LABELS = (*_RESOURCE_LABELS, "guard", "server")

_VALIDATOR_HOST = "127.0.0.1"
_VALIDATOR_PORT = 8011


class _ResourceTripwireError(Exception):
    """Sentinel raised by a stubbed resource-construction seam."""


class _UnsettledGuardRejectionError(Exception):
    """Placeholder so pre-implementation runs report cleanly instead of crashing."""


def _install_ordered_spies(order: list[str], counts: dict[str, int]) -> None:
    """
    Install spies that record DSPy guard and resource-construction order.

    Parameters:
        order (list[str]): Mutable sequence receiving guard and resource labels in call order.
        counts (dict[str, int]): Mutable mapping updated with the number of calls for each label.
    """
    import fleet_rlm.app as app_module
    import fleet_rlm.cli as cli_module
    import fleet_rlm.composition.live as composition_daytona
    import fleet_rlm.composition.testing as composition_common
    import fleet_rlm.daytona.platform as daytona_platform
    import fleet_rlm.persistence.database as persistence_database
    import fleet_rlm.rlm.compat_3_3_1 as dspy_compat
    import fleet_rlm.rlm.program as program

    real_guard = dspy_compat.assert_dspy_version

    def recording_guard() -> None:
        order.append("guard")
        counts["guard"] += 1
        real_guard()

    # ``composition.testing`` binds the guard at module import time, while
    # ``composition.live``, ``fleet_rlm.app``, and the CLI rebind it lazily
    # at call time; patch both binding styles.
    dspy_compat.assert_dspy_version = recording_guard
    composition_common.assert_dspy_version = recording_guard
    composition_daytona.assert_dspy_version = recording_guard
    app_module.assert_dspy_version = recording_guard
    cli_module.assert_dspy_version = recording_guard

    seam_bindings = (
        (persistence_database, "create_async_engine_from_url", "database"),
        (daytona_platform, "build_daytona_client", "daytona"),
        (program, "build_model_bundle", "provider"),
    )
    for module, attribute, label in seam_bindings:

        def _make_tripwire(label: str) -> Any:
            def tripwire(*args: Any, **kwargs: Any) -> Any:
                del args, kwargs
                order.append(label)
                counts[label] += 1
                raise _ResourceTripwireError(label)

            return tripwire

        setattr(module, attribute, _make_tripwire(label))


def _stub_daytona_settings_gates() -> None:
    """Keep the composition entry credential-free after the guard succeeds.

    Both stubs replace pure configuration gates (settings and sandbox spec
    derivation); no provider, database, or Daytona resource seam is touched.
    ``build_daytona_composition`` lazy-imports the spec helper inside the
    function, so the stub lives on its source module; ``require_daytona_settings``
    is a module-global resolved at call time.
    """
    import fleet_rlm.composition.live as composition_daytona
    import fleet_rlm.daytona.provisioning as daytona_provisioning

    composition_daytona.require_daytona_settings = lambda _settings: None
    daytona_provisioning.sandbox_spec_from_settings = lambda _settings: object()


def _run_create_app(*, payload: dict[str, Any]) -> None:
    from fleet_rlm.app import create_app
    from fleet_rlm.config.settings import Settings

    create_app(settings=Settings(run_environment="daytona"))
    payload["outcome"] = "accepted"


def _run_composition_local(*, payload: dict[str, Any]) -> None:
    from fastapi import FastAPI

    from fleet_rlm.composition.testing import install_testing_composition
    from fleet_rlm.config.settings import Settings
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    app = FastAPI()
    app.state.skill_catalog = build_bundled_skill_catalog()
    install_testing_composition(app, Settings(run_environment="daytona"))
    payload["outcome"] = "accepted"


def _run_composition_daytona(*, payload: dict[str, Any]) -> None:
    from fleet_rlm.composition import live as composition_daytona
    from fleet_rlm.config.settings import Settings
    from fleet_rlm.skills.catalog import build_bundled_skill_catalog

    _stub_daytona_settings_gates()
    try:
        asyncio.run(
            composition_daytona.build_daytona_composition(
                Settings(run_environment="daytona"),
                skill_catalog=build_bundled_skill_catalog(),
            )
        )
    except _ResourceTripwireError:
        # The composition advanced past the guard into the first (stubbed)
        # resource construction, proving guard-before-resources ordering.
        payload["outcome"] = "accepted"
        return
    payload["outcome"] = "accepted"
    payload["harness_note"] = "composition completed without touching a resource seam"


def _run_cli(order: list[str], counts: dict[str, int], argv: list[str], *, entrypoint: str) -> dict[str, Any]:
    import uvicorn

    def recording_serve(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        order.append("server")
        counts["server"] += 1

    uvicorn.run = recording_serve

    from fleet_rlm.cli import main as cli_main

    launcher = cli_main.fleet_rlm_main if entrypoint == "fleet-rlm" else cli_main.fleet_main

    stderr = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stderr(stderr):
        try:
            launcher(argv)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
    return {
        "outcome": "accepted" if exit_code == 0 else "rejected",
        "cli_exit_code": exit_code,
        "cli_stderr": stderr.getvalue(),
        "process_exit_code": exit_code,
    }


def _run_cli_serve_api(order: list[str], counts: dict[str, int], *, payload: dict[str, Any]) -> None:
    payload.update(
        _run_cli(
            order,
            counts,
            ["serve-api", "--host", _VALIDATOR_HOST, "--port", str(_VALIDATOR_PORT)],
            entrypoint="fleet-rlm",
        )
    )


def _run_cli_web(order: list[str], counts: dict[str, int], *, payload: dict[str, Any]) -> None:
    payload.update(
        _run_cli(
            order,
            counts,
            ["web", "--host", _VALIDATOR_HOST, "--port", str(_VALIDATOR_PORT)],
            entrypoint="fleet",
        )
    )


_MODES = {
    "create-app": _run_create_app,
    "composition-local": _run_composition_local,
    "composition-daytona": _run_composition_daytona,
    "cli-serve-api": _run_cli_serve_api,
    "cli-web": _run_cli_web,
}


def _report(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    """
    Run the selected DSPy version-guard test mode and report its outcome.

    Parameters:
        argv (list[str]): Command-line arguments containing the harness name, mode, and reported DSPy version.

    Returns:
        int: The process exit code recorded by the selected mode, or a harness status
        code for invalid arguments, guard rejection, or unexpected errors.
    """
    if len(argv) != 3 or argv[1] not in _MODES:
        sys.stderr.write("usage: _dspy_version_guard_spy.py <" + "|".join(sorted(_MODES)) + "> <reported-version>\n")
        return _HARNESS_ERROR
    mode, reported_version = argv[1], argv[2]

    # Inject the reported runtime BEFORE any Fleet module reads it.
    import dspy

    dspy.__version__ = reported_version

    import fleet_rlm.rlm.compat_3_3_1 as dspy_compat

    rejection_error_type = getattr(
        dspy_compat,
        "UncertifiedDSpyVersionError",
        _UnsettledGuardRejectionError,
    )

    order: list[str] = []
    counts = {label: 0 for label in _COUNTER_LABELS}
    _install_ordered_spies(order, counts)

    payload: dict[str, Any] = {
        "mode": mode,
        "reported_version": reported_version,
        "order": order,
        "counts": counts,
    }
    runner = _MODES[mode]
    try:
        if mode.startswith("cli-"):
            runner(order, counts, payload=payload)
        else:
            runner(payload=payload)
    except _ResourceTripwireError:
        payload["outcome"] = "accepted"
    except rejection_error_type as exc:
        payload.update(outcome="rejected", error_type=type(exc).__name__, error_message=str(exc))
        _report(payload)
        sys.stderr.write(f"{exc}\n")
        return _GUARD_REJECTED
    except Exception as exc:  # private spy lane must capture every failure shape
        payload.update(outcome="error", error_type=type(exc).__name__, error_message=str(exc)[:240])
        _report(payload)
        return _HARNESS_ERROR
    payload.setdefault("outcome", "accepted")
    _report(payload)
    return int(payload.get("process_exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
